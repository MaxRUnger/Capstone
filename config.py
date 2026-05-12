import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _env_list(name: str, default: str = ""):
    raw = os.environ.get(name, default)
    return [v.strip() for v in str(raw).split(",") if v.strip()]


class Config:
    APP_ENV = os.environ.get("APP_ENV", "development").strip().lower()
    IS_PRODUCTION = APP_ENV in ("prod", "production")

    SECRET_KEY = os.environ.get("SECRET_KEY")
    if IS_PRODUCTION and not SECRET_KEY:
        raise RuntimeError("SECRET_KEY is required in production.")
    if not SECRET_KEY:
        # Development fallback only.
        SECRET_KEY = "dev-secret-key-local-only"

    SUPABASE_URL = os.environ.get('SUPABASE_URL')
    SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
    # Public base URL for QR / phone upload links. Set on production, e.g. https://claritygrader.net
    # (no trailing slash). If unset and you open the app via localhost, the server tries your LAN IP.
    PUBLIC_BASE_URL = os.environ.get('PUBLIC_BASE_URL')
    # Redirect URLs for OAuth (update these with your actual domain)
    REDIRECT_URL = os.environ.get('REDIRECT_URL') or 'http://localhost:5000/auth/callback'

    # Session cookie hardening.
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", IS_PRODUCTION)
    SESSION_COOKIE_HTTPONLY = _env_bool("SESSION_COOKIE_HTTPONLY", True)
    SESSION_COOKIE_SAMESITE = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax")
    SESSION_COOKIE_NAME = os.environ.get("SESSION_COOKIE_NAME", "clarity_session")
    PERMANENT_SESSION_LIFETIME = timedelta(
        minutes=int(os.environ.get("SESSION_LIFETIME_MINUTES", "480"))
    )
    SESSION_REFRESH_EACH_REQUEST = _env_bool("SESSION_REFRESH_EACH_REQUEST", True)

    # CORS strict allowlist (credentials-enabled).
    # Railway / prod should set this explicitly, e.g. https://app.example.com
    CORS_ALLOWED_ORIGINS = _env_list(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:5000,http://127.0.0.1:5000",
    )