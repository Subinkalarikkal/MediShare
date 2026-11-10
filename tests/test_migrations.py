"""Alembic migrations build the same schema as the ORM models."""
import os
import subprocess
import sys
from pathlib import Path

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]


def _alembic(*args, url):
    env = dict(os.environ, DATABASE_URL=url, PYTHONUTF8="1")
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env, capture_output=True, text=True)


def test_upgrade_check_downgrade(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    assert _alembic("upgrade", "head", url=url).returncode == 0
    tables = set(sa.inspect(sa.create_engine(url)).get_table_names()) - {"alembic_version"}
    from database.models import ALL_TABLES
    assert tables == set(ALL_TABLES) and len(tables) == 31
    check = _alembic("check", url=url)
    assert check.returncode == 0, check.stdout + check.stderr
    assert _alembic("downgrade", "base", url=url).returncode == 0


def test_postgresql_offline_sql_renders():
    res = _alembic("upgrade", "head", "--sql", url="postgresql+psycopg2://u:p@localhost/medishare")
    assert res.returncode == 0, res.stderr
    assert res.stdout.count("CREATE TABLE") == 32  # 31 tables + alembic_version
    assert "ALTER TABLE users ADD CONSTRAINT fk_users_organization_id" in res.stdout


def test_startup_upgrades_old_untracked_database(tmp_path, monkeypatch):
    """A live database from an older version (no username column, no alembic_version) is upgraded on start."""
    from database import connection
    from database.migrations import run_migrations
    url = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    assert _alembic("upgrade", "945b2423f698", url=url).returncode == 0
    eng = sa.create_engine(url)
    with eng.begin() as c:
        c.execute(sa.text("DROP TABLE alembic_version"))  # simulate a schema created without Alembic
        c.execute(sa.text("INSERT INTO roles (id, code, name, created_at) VALUES "
                          "('00000000000000000000000000000001', 'X', 'x', '2026-01-01')"))
    eng.dispose()
    connection.configure(url)
    run_migrations()
    insp = sa.inspect(connection.get_engine())
    assert "username" in {c["name"] for c in insp.get_columns("users")}
    assert "purpose" in {c["name"] for c in insp.get_columns("email_login_codes")}
    with connection.get_engine().connect() as c:
        assert c.execute(sa.text("SELECT count(*) FROM roles")).scalar() == 1  # existing data kept
    connection.get_engine().dispose()


def test_bootstrap_cache_is_keyed_by_schema_version():
    import inspect as pyinspect
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "def _bootstrap(schema_head: str, db_url: str, admin_seed: str)" in src
    assert "_bootstrap(head_revision()," in src
