from __future__ import annotations

import json
import sys
from typing import Any

from strong_sync import config
from strong_sync.api import StrongClient

SECRET_HINTS = ("password", "token", "secret", "authorization", "jwt", "email", "cookie")


def redact(obj: Any, depth: int = 0, truncate_lists: bool = True) -> Any:
    """Strip secrets. `truncate_lists` keeps schema dumps short, but MUST be
    False when saving a fixture or you silently lose most of the records."""
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if any(hint in key.lower() for hint in SECRET_HINTS):
                out[key] = f"<redacted:{type(value).__name__}>"
            else:
                out[key] = redact(value, depth + 1, truncate_lists)
        return out
    if isinstance(obj, list):
        items = obj[:3] if truncate_lists else obj
        return [redact(v, depth + 1, truncate_lists) for v in items]
    return obj


def describe(obj: Any, indent: int = 0, max_depth: int = 4) -> str:
    pad = "  " * indent
    if indent > max_depth:
        return f"{pad}..."
    if isinstance(obj, dict):
        lines = []
        for key, value in obj.items():
            if isinstance(value, (dict, list)):
                lines.append(f"{pad}{key}: {type(value).__name__}")
                lines.append(describe(value, indent + 1, max_depth))
            else:
                preview = str(value)
                if len(preview) > 60:
                    preview = preview[:57] + "..."
                lines.append(f"{pad}{key}: {type(value).__name__} = {preview}")
        return "\n".join(lines)
    if isinstance(obj, list):
        if not obj:
            return f"{pad}(empty list)"
        return f"{pad}[0 of {len(obj)}]:\n" + describe(obj[0], indent + 1, max_depth)
    return f"{pad}{obj!r}"


def connect() -> StrongClient:
    client = StrongClient()
    token, user_id = config.static_token()
    if token and user_id:
        client.use_static_token(token, user_id)
        return client
    username, password = config.credentials()
    client.login(username, password)
    return client


def cmd_login() -> None:
    client = connect()
    print("=== LOGIN RESPONSE (redacted) ===")
    print(json.dumps(redact(client.raw_login), indent=2))
    print("\n=== TOP-LEVEL KEYS ===")
    print(sorted(client.raw_login))
    print("\n=== RESOLVED userId ===")
    print(client.user_id)
    print("\n=== AUTH HEADER SET? ===")
    print("Authorization" in client.session.headers)
    print("\n=== COOKIES ===")
    print(list(client.session.cookies.keys()))
    if client.expires_in:
        print(f"\n=== TOKEN LIFETIME ===\n{client.expires_in}s (~{client.expires_in // 60} min)")


def cmd_schema() -> None:
    client = connect()
    page = next(client.sync_pages(["log", "measurement"], limit=5))
    embedded = page.get("_embedded", {}) or {}

    for kind in ("log", "measurement"):
        items = embedded.get(kind) or []
        print(f"\n{'=' * 30}\n{kind.upper()} — {len(items)} on this page\n{'=' * 30}")
        if not items:
            print("(none returned — try a larger limit or --full)")
            continue
        print(describe(redact(items[0])))
        print(f"\n--- raw {kind}[0] ---")
        print(json.dumps(redact(items[0]), indent=2)[:4000])


def cmd_counts() -> None:
    client = connect()
    totals: dict[str, int] = {}
    pages = 0
    for page in client.sync_pages(
        ["log", "metric", "measurement", "template", "metricCache"]
    ):
        pages += 1
        for kind, items in (page.get("_embedded", {}) or {}).items():
            totals[kind] = totals.get(kind, 0) + len(items or [])
    print(f"pages: {pages}")
    for kind, count in sorted(totals.items()):
        print(f"{kind:15} {count}")
    print(f"\nfinal continuation: {getattr(client, 'last_continuation', None)}")


def cmd_fixture() -> None:
    client = connect()
    page = next(client.sync_pages(["log", "measurement"], limit=50))
    out = config.ROOT / "tests" / "fixtures" / "live_sample.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps([redact(page, truncate_lists=False)], indent=2), encoding="utf-8"
    )
    embedded = page.get("_embedded", {}) or {}
    print(f"logs: {len(embedded.get('log') or [])}, "
          f"measurements: {len(embedded.get('measurement') or [])}")
    print(f"Wrote {out}")
    print("Review it for anything personal BEFORE committing.")
    print(f"Then: python -m strong_sync.cli sync --fixture {out} --dry-run")


PROBE_VARIANTS: list[tuple[str, dict[str, str]]] = [
    ("real Strong iOS client", {
        "User-Agent": "Strong iOS",
        "X-Client-Version": "6.4.3",
        "X-Client-Build": "8343",
        "X-Client-Platform": "ios",
    }),
    ("UA only, no X-Client-*", {"User-Agent": "Strong iOS"}),
    ("X-Client-* only, default UA", {
        "X-Client-Version": "6.4.3",
        "X-Client-Build": "8343",
        "X-Client-Platform": "ios",
    }),
    ("bare requests default", {}),
    ("curl UA", {"User-Agent": "curl/8.4.0"}),
    ("desktop browser UA", {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
    }),
]


def cmd_probe() -> None:
    """Find out WHO is rejecting you: the edge, or the auth code.

    Deliberately uses a wrong password. A 401/400 with a JSON body is a WIN --
    it means the request reached the application. A short non-JSON 403 means an
    edge block. Kept to a handful of attempts to avoid tripping rate limits.
    """
    import requests

    from strong_sync import config as cfg

    username, _ = cfg.credentials()
    payload = {"usernameOrEmail": username, "password": "probe-invalid-password"}

    print("Sending a deliberately WRONG password to classify the rejection.\n")
    for label, extra in PROBE_VARIANTS:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        headers.update(extra)
        try:
            resp = requests.post(cfg.login_url(), json=payload, headers=headers, timeout=20)
        except requests.RequestException as exc:
            print(f"{label:32} ERROR {type(exc).__name__}: {exc}")
            continue

        body = (resp.text or "").strip()
        is_json = body.startswith("{")
        if resp.status_code in (400, 401) or is_json:
            verdict = "REACHED THE APP  <-- use these headers"
        elif resp.status_code == 403:
            verdict = "blocked at the edge"
        else:
            verdict = "unclear"
        notable = {h: resp.headers[h] for h in ("server", "cf-ray", "cf-mitigated") if h in resp.headers}
        print(f"{label:32} {resp.status_code}  {verdict}")
        print(f"{'':32} body: {body[:90] or '(empty)'}")
        if notable:
            print(f"{'':32} {notable}")

    print(
        "\nIf EVERY variant is 403, the block is not header-based -- it is likely "
        "TLS-fingerprint or IP based. requests cannot change its TLS fingerprint; "
        "try curl, or capture the real app's request (TESTING.md Phase 1b)."
    )


COMMANDS = {
    "login": cmd_login,
    "probe": cmd_probe,
    "schema": cmd_schema,
    "counts": cmd_counts,
    "fixture": cmd_fixture,
}

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "login"
    if name not in COMMANDS:
        sys.exit(f"Unknown command {name!r}. Choose from: {', '.join(COMMANDS)}")
    COMMANDS[name]()
