from __future__ import annotations

import logging
import os
from typing import Any, Iterator

import requests

from . import config

log = logging.getLogger(__name__)

TOKEN_KEYS = ("token", "accessToken", "access_token", "jwt", "idToken", "id_token", "sessionToken")
USER_ID_KEYS = ("userId", "user_id", "id", "uuid")

DEFAULT_USER_AGENT = os.environ.get("STRONG_USER_AGENT", "Strong iOS")
CLIENT_VERSION = os.environ.get("STRONG_CLIENT_VERSION", "6.4.3")
CLIENT_BUILD = os.environ.get("STRONG_CLIENT_BUILD", "8343")
CLIENT_PLATFORM = os.environ.get("STRONG_CLIENT_PLATFORM", "ios")

DIAGNOSTIC_HEADERS = (
    "server", "cf-ray", "cf-mitigated", "x-amzn-errortype", "x-amz-cf-id",
    "x-cache", "via", "retry-after", "www-authenticate",
)


class StrongAPIError(RuntimeError):
    pass


class MFARequiredError(StrongAPIError):
    """The server issued an MFA challenge instead of authenticating us."""

    def __init__(self, info: dict[str, Any]):
        self.challenge = info.get("challenge")
        self.redirect_url = info.get("redirectUrl")
        self.expires_at = info.get("expiresAt")
        super().__init__(
            "MFA challenge issued instead of a session.\n"
            f"  challenge:  {self.challenge}\n"
            f"  verify at:  {self.redirect_url}\n"
            f"  expires:    {self.expires_at}\n\n"
            "This usually means the server does not recognize your deviceId.\n"
            "Fix, in order of effort:\n"
            "  1. Set STRONG_DEVICE_ID to the UUID your phone sends (see your proxy\n"
            "     capture of /auth/login). A trusted device is not challenged.\n"
            "  2. Run `python -m tools.explore mfa` to complete the challenge once\n"
            "     interactively and save the resulting session.\n"
            "See TESTING.md Phase 1c."
        )


class StrongClient:
    def __init__(self, timeout: int = 30):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "User-Agent": DEFAULT_USER_AGENT,
                "X-Client-Version": CLIENT_VERSION,
                "X-Client-Build": CLIENT_BUILD,
                "X-Client-Platform": CLIENT_PLATFORM,
                "Connection": "keep-alive",
            }
        )
        self.session.headers.update(config.extra_headers())
        self.timeout = timeout
        self.user_id: str | None = None
        self.raw_login: dict[str, Any] = {}
        self._creds: tuple[str, str] | None = None
        self.expires_in: int | None = None

    def use_static_token(self, token: str, user_id: str) -> None:
        """Skip /auth/login using a credential captured from the app."""
        header = os.environ.get("STRONG_TOKEN_HEADER", "Authorization")
        scheme = os.environ.get("STRONG_TOKEN_SCHEME", "Bearer ")
        self.session.headers[header] = f"{scheme}{token}" if scheme else token
        self.user_id = user_id
        log.info("Using captured token (%s header); skipping login.", header)

    # ------------------------------------------------------------------ auth

    def login(self, username_or_email: str, password: str) -> dict[str, Any]:
        self._creds = (username_or_email, password)
        payload = {
            "usernameOrEmail": username_or_email,
            "password": password,
            "deviceId": config.device_id(username_or_email),
        }
        resp = self.session.post(config.login_url(), json=payload, timeout=self.timeout)
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except ValueError:
                body = {}
            if isinstance(body, dict) and (
                body.get("code") == "MFA_REQUIRED" or body.get("challenge")
            ):
                raise MFARequiredError(body)
            raise StrongAPIError(_describe_failure(resp))
        try:
            data = resp.json()
        except ValueError:
            raise StrongAPIError(
                "Login returned 200 but the body was not JSON. First 300 chars:\n"
                f"{resp.text[:300]}"
            )
        self.raw_login = data

        self.user_id = _first_present(data, USER_ID_KEYS)
        if not self.user_id:
            raise StrongAPIError(
                "Login succeeded but no userId found in the response. "
                f"Top-level keys were: {sorted(data)}"
            )

        token = _first_present(data, TOKEN_KEYS)
        self.expires_in = data.get("expiresIn")
        if isinstance(self.expires_in, int):
            log.info("Access token valid for %ds (~%d min).", self.expires_in, self.expires_in // 60)
        auth_cookies = [c for c in self.session.cookies.keys() if "Identity" in c or "Auth" in c]
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
            log.info("Authenticated with bearer token.")
        elif auth_cookies:
            # This backend is ASP.NET Core Identity: auth rides on the
            # .AspNetCore.Identity.Application cookie, and ARRAffinity /
            # ASLBSA pin you to a backend instance. requests.Session carries
            # all of them automatically -- do not create a second Session.
            log.info("Authenticated via cookies: %s", auth_cookies)
        elif self.session.cookies:
            log.warning(
                "Only non-auth cookies were set (%s). Sync may 401.",
                list(self.session.cookies.keys()),
            )
        else:
            log.warning(
                "No auth token and no cookies were returned. Sync requests will "
                "probably 401. Inspect the login response with tools/explore.py."
            )
        return data

    # ------------------------------------------------------------------ sync

    def _get_with_reauth(self, url: str, params):
        """GET, and if the 20-minute access token has expired, log in again.

        A --full-resync over years of history can easily outlive one token.
        Re-logging in is simpler and more reliable than guessing the refresh
        endpoint, and it costs one extra request at most once per 20 minutes.
        """
        resp = self.session.get(url, params=params or None, timeout=self.timeout)
        if resp.status_code != 401:
            return resp
        if not self._creds:
            raise StrongAPIError(
                "401 during sync and no credentials held, so we cannot re-authenticate. "
                "If you are using STRONG_TOKEN, it has expired -- capture a fresh one."
            )
        log.info("Access token expired mid-sync; re-authenticating.")
        self.login(*self._creds)
        return self.session.get(url, params=params or None, timeout=self.timeout)

    def sync_pages(
        self,
        includes: list[str],
        continuation: str | None = None,
        limit: int | None = None,
        max_pages: int = 200,
    ) -> Iterator[dict[str, Any]]:
        """Yield raw sync payloads, following ``_links.next`` until exhausted.

        The final page's continuation token is available as
        ``self.last_continuation`` once iteration completes.
        """
        if not self.user_id:
            raise StrongAPIError("Call login() before sync_pages().")

        limit = limit or config.PAGE_LIMIT
        url = f"{config.api_base()}/api/users/{self.user_id}/"
        params: list[tuple[str, str]] = [("include", i) for i in includes]
        params.append(("limit", str(limit)))
        if continuation:
            params.append(("continuation", continuation))

        self.last_continuation = continuation
        pages = 0

        while url and pages < max_pages:
            resp = self._get_with_reauth(url, params)
            if resp.status_code >= 400:
                raise StrongAPIError(
                    f"Sync failed ({resp.status_code}). Body: {resp.text[:400]}"
                )
            data = resp.json()
            pages += 1
            yield data

            next_href = (data.get("_links", {}).get("next") or {}).get("href")
            if not next_href:
                break
            self.last_continuation = _continuation_from(next_href)
            # The next href already carries every query param we need.
            url = next_href if next_href.startswith("http") else config.api_base() + next_href
            params = []

        if pages >= max_pages:
            log.warning("Stopped after %d pages (max_pages guard).", max_pages)


def _describe_failure(resp) -> str:
    """Build an error message that distinguishes an edge block from an auth failure."""
    seen = {h: resp.headers[h] for h in DIAGNOSTIC_HEADERS if h in resp.headers}
    body = (resp.text or "").strip()
    lines = [f"Login failed ({resp.status_code}). Body: {body[:400] or '(empty)'}"]

    if seen:
        lines.append("Response headers of interest: " + ", ".join(f"{k}={v}" for k, v in seen.items()))

    looks_like_edge = (
        resp.status_code == 403
        and len(body) < 200
        and not body.startswith("{")
    )
    if looks_like_edge:
        lines.append(
            "\nThis looks like a firewall/CDN block rather than a rejected password: "
            "a short non-JSON 403 usually comes from the edge, before the auth code runs.\n"
            "Try, in order:\n"
            "  1. python -m tools.explore probe   (tests several header sets)\n"
            "  2. Reproduce with curl — if curl works and Python does not, it is headers.\n"
            "  3. If curl also fails, capture what the Strong app itself sends "
            "(see TESTING.md Phase 1b)."
        )
    return "\n".join(lines)


def _first_present(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = data.get(key)
        if value:
            return value
    # One level deep — some APIs nest under "user" or "data".
    for container in ("user", "data", "result"):
        nested = data.get(container)
        if isinstance(nested, dict):
            for key in keys:
                if nested.get(key):
                    return nested[key]
    return None


def _continuation_from(href: str) -> str | None:
    from urllib.parse import parse_qs, urlparse

    values = parse_qs(urlparse(href).query).get("continuation")
    return values[0] if values else None
