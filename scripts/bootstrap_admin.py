"""Secure bootstrap of the first super administrator.

Run once from a trusted shell after migrations:
    python scripts/bootstrap_admin.py --email you@example.org --name "Your Name"
(or set BOOTSTRAP_SUPER_ADMIN_EMAIL). Refuses to run if a super admin exists.
The account is linked to the email address; the person signs in via OIDC.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import get_settings  # noqa: E402
from database.connection import init_db, session_scope  # noqa: E402
from services.auth_service import bootstrap_super_admin  # noqa: E402
from services.errors import PermissionDenied, ValidationError  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--email", default=get_settings().bootstrap_super_admin_email)
    p.add_argument("--name", default="Super Administrator")
    p.add_argument("--create-tables", action="store_true", help="Dev only: create tables instead of using Alembic")
    a = p.parse_args()
    if not a.email:
        sys.exit("Provide --email or BOOTSTRAP_SUPER_ADMIN_EMAIL.")
    init_db(create_all=a.create_tables)  # seeds roles/permissions/settings
    try:
        with session_scope() as s:
            u = bootstrap_super_admin(s, a.email, a.name)
            print(f"Super administrator created for {u.email}.")
    except (PermissionDenied, ValidationError) as exc:
        sys.exit(f"Bootstrap refused: {exc}")


if __name__ == "__main__":
    main()
