#!/usr/bin/env python3
"""Fail closed unless every active administrator has enabled TOTP."""

from __future__ import annotations

import json

from backend.database import SessionLocal
from backend.models import AdminTotpSecret, AdminUser


def inspect_admin_mfa(db) -> dict:
    admins = (
        db.query(AdminUser)
        .filter(AdminUser.active.is_(True))
        .order_by(AdminUser.id)
        .all()
    )
    enabled_admin_ids = {
        row.admin_id
        for row in db.query(AdminTotpSecret)
        .filter(AdminTotpSecret.enabled.is_(True))
        .all()
    }
    missing = [admin.email for admin in admins if admin.id not in enabled_admin_ids]
    return {
        "ok": bool(admins) and not missing,
        "active_admins": len(admins),
        "missing_mfa": missing,
    }


def main() -> int:
    db = SessionLocal()
    try:
        result = inspect_admin_mfa(db)
    finally:
        db.close()

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if not result["active_admins"]:
        print("No active administrator exists; provision one before production deploy.")
        return 2
    if result["missing_mfa"]:
        print(
            "Production administrator MFA is not enabled for: "
            + ", ".join(result["missing_mfa"])
        )
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
