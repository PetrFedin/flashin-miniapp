#!/usr/bin/env python3
"""Create only the first administrator, with no persisted production password."""

from __future__ import annotations

import argparse
import getpass
import json
import sys

from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.database import SessionLocal
from backend.models import AdminUser
from backend.security import hash_password
from backend.services.admin_password_policy import validate_admin_password
from backend.services.audit import log_admin_action

ACK_FLAG = "--acknowledge-production-admin-bootstrap"


class SeedAdminError(RuntimeError):
    pass


def seed_first_admin(
    db: Session,
    *,
    email: str,
    password: str,
) -> tuple[AdminUser, bool]:
    normalized_email = (email or "").strip().lower()
    if len(normalized_email) < 3 or len(normalized_email) > 255 or "@" not in normalized_email:
        raise SeedAdminError("Administrator email is invalid")

    try:
        validate_admin_password(password, normalized_email)
    except ValueError as exc:
        raise SeedAdminError(str(exc)) from exc

    # Lock the administrator set before deciding whether a first owner may be
    # created. Once this table is non-empty, this operator script must never be
    # usable as an offline path for adding another privileged account.
    admins = (
        db.query(AdminUser)
        .order_by(AdminUser.id)
        .with_for_update()
        .all()
    )
    if admins:
        existing = next(
            (admin for admin in admins if admin.email.strip().lower() == normalized_email),
            None,
        )
        if existing is None:
            raise SeedAdminError(
                "Administrator bootstrap is closed because an administrator already exists"
            )
        if not existing.active or existing.role != "owner":
            raise SeedAdminError(
                "Existing bootstrap administrator is not an active owner; use the authenticated security workflow or documented recovery process"
            )
        return existing, False

    admin = AdminUser(
        email=normalized_email,
        password_hash=hash_password(password),
        role="owner",
        active=True,
    )
    db.add(admin)
    db.flush()
    log_admin_action(
        db,
        None,
        "admin.bootstrap.create",
        "admin_user",
        admin.id,
        {"role": "owner", "operator_path": "offline_first_admin"},
    )
    return admin, True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create the first FLASHIN administrator")
    parser.add_argument(
        "--email",
        default="",
        help="First administrator email; defaults to ADMIN_EMAIL",
    )
    parser.add_argument(
        ACK_FLAG,
        action="store_true",
        dest="acknowledge",
        help="Explicitly acknowledge production first-admin bootstrap",
    )
    return parser


def _prompt_production_password() -> str:
    if not sys.stdin.isatty():
        raise SeedAdminError("Production administrator bootstrap requires an interactive terminal")
    try:
        first = getpass.getpass("First administrator password (hidden): ")
        second = getpass.getpass("Confirm administrator password (hidden): ")
    except (EOFError, KeyboardInterrupt) as exc:
        raise SeedAdminError("Administrator bootstrap cancelled") from exc
    if first != second:
        raise SeedAdminError("Administrator passwords do not match")
    return first


def main() -> int:
    args = build_parser().parse_args()
    try:
        settings = get_settings()
    except Exception as exc:
        print(f"Runtime configuration is invalid: {exc}", file=sys.stderr)
        return 2

    production = settings.app_env.strip().lower() == "production"
    if production and not args.acknowledge:
        print(f"Refusing production bootstrap without {ACK_FLAG}", file=sys.stderr)
        return 2

    email = (args.email or settings.admin_email).strip().lower()
    try:
        password = _prompt_production_password() if production else settings.admin_password
        if not password:
            raise SeedAdminError(
                "Local administrator password is missing; configure ADMIN_PASSWORD outside production"
            )
    except SeedAdminError as exc:
        print(f"Administrator bootstrap refused: {exc}", file=sys.stderr)
        return 2

    db = SessionLocal()
    try:
        admin, created = seed_first_admin(db, email=email, password=password)
        db.commit()
        result = {
            "ok": True,
            "admin_id": int(admin.id),
            "email": str(admin.email),
            "role": str(admin.role),
            "created": created,
        }
    except SeedAdminError as exc:
        db.rollback()
        print(f"Administrator bootstrap refused: {exc}", file=sys.stderr)
        return 2
    except Exception:
        db.rollback()
        print("Administrator bootstrap failed; transaction was rolled back", file=sys.stderr)
        return 2
    finally:
        db.close()

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
