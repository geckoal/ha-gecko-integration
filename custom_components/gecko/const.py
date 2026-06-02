"""Constants for the Gecko integration."""

from pathlib import Path

DOMAIN = "gecko"

# --- Auth & Tenant Defaults ---
_DEFAULT_OAUTH2_CLIENT_ID = "L81oh6hgUsvMg40TgTGoz4lxNy8eViM0"
_DEFAULT_AUTH0_URL_BASE = "https://gecko-prod.us.auth0.com"
_DEFAULT_API_BASE_URL = "https://api.geckowatermonitor.com"


def _load_env_overrides() -> dict[str, str]:
    """Load overrides from a .env file next to this module (not committed to git)."""
    env_path = Path(__file__).parent / ".env"
    overrides: dict[str, str] = {}
    try:
        if env_path.is_file():
            with env_path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    key, _, value = line.partition("=")
                    if value:
                        overrides[key.strip()] = value.strip().strip("\"'")
    except OSError:
        pass
    return overrides


_env = _load_env_overrides()

OAUTH2_CLIENT_ID = _env.get("GECKO_OAUTH2_CLIENT_ID", _DEFAULT_OAUTH2_CLIENT_ID)
AUTH0_URL_BASE = _env.get("GECKO_AUTH0_URL_BASE", _DEFAULT_AUTH0_URL_BASE)
API_BASE_URL = _env.get("GECKO_API_BASE_URL", _DEFAULT_API_BASE_URL)

OAUTH2_AUTHORIZE = f"{AUTH0_URL_BASE}/authorize"
OAUTH2_TOKEN = f"{AUTH0_URL_BASE}/oauth/token"

# Client configuration
CONFIG_TIMEOUT = 30.0  # Default timeout for GeckoIotClient configuration loading in seconds
