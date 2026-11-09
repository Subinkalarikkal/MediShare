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


def _secret(name: str, default: str | None = None) -> str | None:
    """Read a setting from env first, then Streamlit secrets (if available)."""
    value = os.getenv(name)
    if value not in (None, ""):
        return value
    try:
        import streamlit as st

        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return default


def _bool(name: str, default: bool = False) -> bool:
    raw = _secret(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_env: str = field(default_factory=lambda: _secret("APP_ENV", "development"))
    database_url: str = field(
        default_factory=lambda: _secret(
            "DATABASE_URL", f"sqlite:///{(BASE_DIR / 'medishare_dev.db').as_posix()}"
        )
    )
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
    # "oidc" uses Streamlit's st.login; "dev" enables the clearly-labelled
    # demo login selector (refused when APP_ENV=production).
    auth_mode: str = field(default_factory=lambda: _secret("AUTH_MODE", "oidc"))
    oidc_provider: str = field(default_factory=lambda: _secret("OIDC_PROVIDER", "google"))
    session_timeout_minutes: int = field(
        default_factory=lambda: int(_secret("SESSION_TIMEOUT_MINUTES", "60"))
    )
    max_upload_mb: int = field(default_factory=lambda: int(_secret("MAX_UPLOAD_MB", "8")))
    bootstrap_super_admin_email: str | None = field(
        default_factory=lambda: _secret("BOOTSTRAP_SUPER_ADMIN_EMAIL")
    )

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def dev_login_enabled(self) -> bool:
        return self.auth_mode == "dev" and not self.is_production

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
