#!/usr/bin/env python3
"""Offline, one-time bootstrap for the first production administrator TOTP factor.

This command intentionally exposes no HTTP route and accepts neither the TOTP
secret nor verification code as command-line arguments. Both are read from a
non-echoing terminal prompt so they do not land in shell history/process args.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys

from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.database import SessionLocal
from backend.models import AdminTotpSecret, AdminUser
from backend.services.admin_security import (
    consume_totp_counter,
    match_totp_counter,
    set_totp_secret,
)
from backend.services.audit import log_admin_action

ACK_FLAG = "--acknowledge-production-mfa-bootstrap"


class BootstrapError(RuntimeError):
    pass


def bootstrap_first_admin_totp(
    db: Session,
    *,
    email: str,
    secret: str,
    verification_code: str,
) -> AdminUser:
    settings = get_settings()
    if settings.app_env.strip().lower() != "production":
        raise BootstrapError("Administrator MFA bootstrap is production-only")

    normalized_email = (email or "").strip().lower()
    if not normalized_email:
        raise BootstrapError("Administrator email is required")

    # Lock the complete administrator set in stable order. This serializes two
    # concurrent first-factor bootstrap attempts even while no TOTP row exists.
    admins = (
        db.query(AdminUser)
        .order_by(AdminUser.id)
        .with_for_update()
        .all()
    )
    target = next(
        (admin for admin in admins if admin.email.strip().lower() == normalized_email),
        None,
    )
    if target is None:
        raise BootstrapError("Administrator does not exist; seed it first")
    if not target.active:
        raise BootstrapError("Administrator is inactive")

    active_ids = {admin.id for admin in admins if admin.active}
    enabled_rows = (
        db.query(AdminTotpSecret)
        .filter(AdminTotpSecret.enabled.is_(True))
        .with_for_update()
        .all()
    )
    if any(row.admin_id in active_ids for row in enabled_rows):
        raise BootstrapError(
            "An active administrator already has MFA; use the authenticated admin security API"
        )

    try:
        matched_counter = match_totp_counter(secret, verification_code)
    except ValueError as exc:
        raise BootstrapError("TOTP secret or verification code is invalid") from exc
    if matched_counter is None:
        raise BootstrapError("TOTP secret or verification code is invalid")

    set_totp_secret(db, target.id, secret, enabled=True)
    if not consume_totp_counter(db, target.id, matched_counter):
        raise BootstrapError("TOTP verification counter could not be consumed")

    log_admin_action(
        db,
        None,
        "admin.totp.bootstrap",
        "admin_user",
        target.id,
        {"enabled": True, "operator_path": "offline_first_admin"},
    )
    return target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bootstrap the first production administrator TOTP factor"
    )
    parser.add_argument(
        "--email",
        default="",
        help="Existing active administrator email; defaults to ADMIN_EMAIL",
    )
    parser.add_argument(
        ACK_FLAG,
        action="store_true",
        dest="acknowledge",
        help="Explicitly acknowledge the one-time production MFA bootstrap",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.acknowledge:
        print(f"Refusing bootstrap without {ACK_FLAG}", file=sys.stderr)
        return 2

    try:
        settings = get_settings()
    except Exception as exc:
        print(f"Production configuration is invalid: {exc}", file=sys.stderr)
        return 2
    if settings.app_env.strip().lower() != "production":
        print("Administrator MFA bootstrap is production-only", file=sys.stderr)
        return 2

    email = (args.email or settings.admin_email).strip().lower()
    try:
        secret = getpass.getpass("TOTP secret (hidden): ").strip()
        verification_code = getpass.getpass("Current 6-digit TOTP code (hidden): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("MFA bootstrap cancelled", file=sys.stderr)
        return 2

    db = SessionLocal()
    try:
        target = bootstrap_first_admin_totp(
            db,
            email=email,
            secret=secret,
            verification_code=verification_code,
        )
        db.commit()
    except BootstrapError as exc:
        db.rollback()
        print(f"MFA bootstrap refused: {exc}", file=sys.stderr)
        return 2
    except Exception:
        db.rollback()
        print("MFA bootstrap failed; transaction was rolled back", file=sys.stderr)
        return 2
    finally:
        db.close()

    print(
        json.dumps(
            {
                "ok": True,
                "admin_id": target.id,
                "email": target.email,
                "totp_enabled": True,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
