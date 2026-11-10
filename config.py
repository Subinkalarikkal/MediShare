"""Central configuration for MediShare.

Values are read from environment variables (optionally loaded from a local
`.env` file) and, when running inside Streamlit, from `st.secrets`.
Secrets are never hard-coded here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # optional in production
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

BASE_DIR = Path(__file__).resolve().parent


def _profile() -> dict:
    """Non-secret deployment profile committed with the code (lowest priority).

    `medishare_profile.toml` makes the public demo work on Streamlit Cloud without any
    secrets. Environment variables and Streamlit secrets always override it, and
    MEDISHARE_PROFILE=none disables it (used by the test suite).
    """
    if os.getenv("MEDISHARE_PROFILE", "").lower() == "none":
        return {}
    path = BASE_DIR / "medishare_profile.toml"
    if not path.exists():
        return {}
    try:
        import tomllib

        return {k: str(v).lower() if isinstance(v, bool) else str(v)
                for k, v in tomllib.loads(path.read_text(encoding="utf-8")).items()}
    except Exception:
        return {}


def _secret(name: str, default: str | None = None) -> str | None:
    """Read a setting from env, then Streamlit secrets, then the committed profile."""
    value = os.getenv(name)
    if value not in (None, ""):
        return value
    try:
        import streamlit as st

        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return _profile().get(name, default)


def _default_sqlite_url() -> str:
    # Production never shares a database file with dev/demo data (which contains SAMPLE records).
    production = (_secret("APP_ENV", "development") or "").lower() == "production"
    name = "medishare.db" if production else "medishare_dev.db"
    return f"sqlite:///{(BASE_DIR / name).as_posix()}"


def _bool(name: str, default: bool = False) -> bool:
    raw = _secret(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_env: str = field(default_factory=lambda: _secret("APP_ENV", "development"))
    database_url: str = field(default_factory=lambda: _secret("DATABASE_URL", _default_sqlite_url()))
    anthropic_api_key: str | None = field(default_factory=lambda: _secret("ANTHROPIC_API_KEY"))
    claude_model: str = field(default_factory=lambda: _secret("CLAUDE_MODEL", "claude-opus-5-5"))
    ai_external_processing_enabled: bool = field(
        default_factory=lambda: _bool("AI_EXTERNAL_PROCESSING_ENABLED", True)
    )
    google_maps_api_key: str | None = field(default_factory=lambda: _secret("GOOGLE_MAPS_API_KEY"))
    storage_endpoint: str | None = field(default_factory=lambda: _secret("STORAGE_ENDPOINT"))
    storage_bucket: str | None = field(default_factory=lambda: _secret("STORAGE_BUCKET"))
    storage_access_key: str | None = field(default_factory=lambda: _secret("STORAGE_ACCESS_KEY"))
    storage_secret_key: str | None = field(default_factory=lambda: _secret("STORAGE_SECRET_KEY"))
    local_storage_dir: str = field(
        default_factory=lambda: _secret("LOCAL_STORAGE_DIR", str(BASE_DIR / ".private_storage"))
    )
    email_provider_api_key: str | None = field(
        default_factory=lambda: _secret("EMAIL_PROVIDER_API_KEY")
    )
    # "email" = one-time code sent by email; "oidc" = Streamlit st.login (Google);
    # "dev" = clearly-labelled local demo login selector (refused when APP_ENV=production).
    auth_mode: str = field(default_factory=lambda: _secret("AUTH_MODE", "oidc"))
    oidc_provider: str = field(default_factory=lambda: _secret("OIDC_PROVIDER", "google"))
    session_timeout_minutes: int = field(
        default_factory=lambda: int(_secret("SESSION_TIMEOUT_MINUTES", "60"))
    )
    max_upload_mb: int = field(default_factory=lambda: int(_secret("MAX_UPLOAD_MB", "8")))
    smtp_host: str | None = field(default_factory=lambda: _secret("SMTP_HOST"))
    smtp_port: int = field(default_factory=lambda: int(_secret("SMTP_PORT", "587")))
    smtp_user: str | None = field(default_factory=lambda: _secret("SMTP_USER"))
    smtp_password: str | None = field(default_factory=lambda: _secret("SMTP_PASSWORD"))
    smtp_from: str | None = field(default_factory=lambda: _secret("SMTP_FROM"))
    smtp_use_ssl: bool = field(default_factory=lambda: _bool("SMTP_USE_SSL", False))
    bootstrap_super_admin_email: str | None = field(
        default_factory=lambda: _secret("BOOTSTRAP_SUPER_ADMIN_EMAIL")
    )
    # Built-in administrator for password sign-in. Only an Argon2id HASH is ever configured –
    # the password itself is never stored in code, settings or the database.
    admin_username: str | None = field(default_factory=lambda: _secret("ADMIN_USERNAME"))
    admin_email: str | None = field(default_factory=lambda: _secret("ADMIN_EMAIL"))
    admin_password_hash: str | None = field(default_factory=lambda: _secret("ADMIN_PASSWORD_HASH"))

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def smtp_enabled(self) -> bool:
        return bool(self.smtp_host and (self.smtp_from or self.smtp_user))

    @property
    def dev_login_enabled(self) -> bool:
        return self.auth_mode == "dev" and not self.is_production

    @property
    def demo_mode(self) -> bool:
        """Public showcase: auto-seed SAMPLE data and use the demo login. Never in production."""
        return _bool("DEMO_MODE", False) and not self.is_production

    @property
    def claude_enabled(self) -> bool:
        return bool(self.anthropic_api_key) and self.ai_external_processing_enabled

    @property
    def s3_enabled(self) -> bool:
        return all(
            [self.storage_bucket, self.storage_access_key, self.storage_secret_key]
        )


def get_settings() -> Settings:
    return Settings()
