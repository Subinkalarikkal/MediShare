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
    assert tables == set(ALL_TABLES) and len(tables) == 30
    check = _alembic("check", url=url)
    assert check.returncode == 0, check.stdout + check.stderr
    assert _alembic("downgrade", "base", url=url).returncode == 0


def test_postgresql_offline_sql_renders():
    res = _alembic("upgrade", "head", "--sql", url="postgresql+psycopg2://u:p@localhost/medishare")
    assert res.returncode == 0, res.stderr
    assert res.stdout.count("CREATE TABLE") == 31  # 30 tables + alembic_version
    assert "ALTER TABLE users ADD CONSTRAINT fk_users_organization_id" in res.stdout
