from __future__ import annotations

import os
from pathlib import Path

# Repo root = parent of the strong_sync package directory.
ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "_cache"
STATE_FILE = ROOT / "state.json"
RUN_COUNT_FILE = DATA_DIR / "run_count.txt"

# The API host is treated as a secret: no default, nothing hardcoded, and it
# never appears in the repo. Supply STRONG_API_BASE via .env locally or a
# GitHub Actions secret in CI. These are functions rather than module-level
# constants so that importing the package never requires the value -- only
# actually making a request does.
def api_base() -> str:
    _load_dotenv()
    value = os.environ.get("STRONG_API_BASE", "").strip().rstrip("/")
    if not value:
        raise SystemExit(
            "STRONG_API_BASE is not set. Add it to your local .env, and as a "
            "repository secret named STRONG_API_BASE for GitHub Actions."
        )
    return value


def login_url() -> str:
    return f"{api_base()}/auth/login"

# How many records to request per sync page.
PAGE_LIMIT = int(os.environ.get("STRONG_PAGE_LIMIT", "200"))

# CONFIRMED: the API stores weights in KILOGRAMS regardless of your account's
# display preference (a 50 lb dumbbell comes back as 22.6796185). Conversion
# happens once, in parsing.py, so cached values are always in WEIGHT_UNIT.
SOURCE_WEIGHT_UNIT = os.environ.get("STRONG_SOURCE_WEIGHT_UNIT", "kg")
WEIGHT_UNIT = os.environ.get("STRONG_WEIGHT_UNIT", "lb")
KG_TO_LB = 2.2046226218

# Warmup sets are excluded from "best set" selection by default.
INCLUDE_WARMUP_SETS = os.environ.get("STRONG_INCLUDE_WARMUP_SETS", "0") == "1"

# --- captured-from-proxy overrides ------------------------------------------
# Headers the real app sends that we can't guess. Set STRONG_EXTRA_HEADERS to a
# JSON object, e.g.  {"x-app-version":"6.1.0","x-platform":"ios"}
# These are merged into every request, and override the defaults on conflict.
def extra_headers() -> dict:
    _load_dotenv()
    raw = os.environ.get("STRONG_EXTRA_HEADERS", "").strip()
    if not raw:
        return {}
    import json

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"STRONG_EXTRA_HEADERS is not valid JSON: {exc}")
    if not isinstance(parsed, dict):
        raise SystemExit("STRONG_EXTRA_HEADERS must be a JSON object.")
    return {str(k): str(v) for k, v in parsed.items()}


def static_token() -> tuple[str | None, str | None]:
    """Bypass /auth/login entirely using a token + userId captured from a proxy.

    Returns (token, user_id). Only usable if the token is long-lived; check the
    `exp` claim if it's a JWT. Prefer real login when it works.
    """
    _load_dotenv()
    token = os.environ.get("STRONG_TOKEN", "").strip() or None
    user_id = os.environ.get("STRONG_USER_ID", "").strip() or None
    return token, user_id



def device_id(username_or_email: str) -> str:
    """A stable device identifier for the login body.

    The iOS app sends its own UUID. Ours must be STABLE across runs -- a new
    value every day looks like a new device logging in, which can trigger
    security emails or device limits. Derived deterministically from the
    account so nothing needs to be stored or kept secret. Override with
    STRONG_DEVICE_ID if you want to reuse a captured one.
    """
    import uuid

    _load_dotenv()
    override = os.environ.get("STRONG_DEVICE_ID", "").strip()
    if override:
        return override
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"strong-sync:{username_or_email.lower()}")).upper()


def credentials() -> tuple[str, str]:
    """Read credentials from the environment, loading .env if present."""
    _load_dotenv()
    user = os.environ.get("STRONG_USERNAME", "").strip()
    pwd = os.environ.get("STRONG_PASSWORD", "").strip()
    if not user or not pwd:
        raise SystemExit(
            "Missing credentials. Set STRONG_USERNAME and STRONG_PASSWORD "
            "in your environment, in a local .env file, or as GitHub Actions "
            "repository secrets."
        )
    return user, pwd


def _load_dotenv() -> None:
    """Load ROOT/.env into os.environ without clobbering real env vars.

    Implemented inline so the package has no hard dependency on python-dotenv.
    """
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        os.environ.setdefault(key, value)
