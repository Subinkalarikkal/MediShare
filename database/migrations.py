"""Apply Alembic migrations at application start-up.

`run_migrations()` upgrades the database the app is connected to up to the latest
revision. Databases created without Alembic (older versions used `create_all`) are
adopted: their current revision is detected from the columns that actually exist,
recorded with `stamp`, and only the missing migrations are applied.
"""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from database.connection import get_engine

ROOT = Path(__file__).resolve().parents[1]

# Oldest → newest. Each entry: (revision, check that the schema already contains that revision's changes).
_REVISIONS = [
    ("964a65b7ecac", lambda t, c: "users" in t),
    ("945b2423f698", lambda t, c: "email_login_codes" in t),
    ("f71541fd27fe", lambda t, c: "username" in c.get("users", set()) and "purpose" in c.get("email_login_codes", set())),
]


def _config() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    url = get_engine().url.render_as_string(hide_password=False)
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def head_revision() -> str:
    return ScriptDirectory.from_config(_config()).get_current_head()


def _detect_untracked_revision(insp) -> str | None:
    tables = set(insp.get_table_names())
    cols = {t: {c["name"] for c in insp.get_columns(t)} for t in ("users", "email_login_codes") if t in tables}
    found = None
    for rev, present in _REVISIONS:
        if present(tables, cols):
            found = rev
        else:
            break
    return found


def run_migrations() -> None:
    engine = get_engine()
    if engine.url.get_backend_name() == "sqlite" and engine.url.database in (None, "", ":memory:"):
        from database.models import Base  # in-memory test databases: no Alembic

        Base.metadata.create_all(engine)
        return
    cfg = _config()
    insp = inspect(engine)
    if "alembic_version" not in insp.get_table_names():
        rev = _detect_untracked_revision(insp)
        if rev:
            command.stamp(cfg, rev)  # adopt an existing schema instead of re-creating it
    command.upgrade(cfg, "head")
