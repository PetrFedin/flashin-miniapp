#!/usr/bin/env python3
"""Fail closed unless every active administrator has enabled TOTP."""

from __future__ import annotations

import json

from backend.database import SessionLocal
from backend.models import AdminTotpSecret, AdminUser

PRODUCTION_COMPOSE = "docker compose -f docker-compose.yml -f docker-compose.production.yml"


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


def _first_admin_bootstrap_instructions() -> str:
    return "\n".join(
        [
            "First production administrator bootstrap is required.",
            "The release is not admitted while this gate is failing.",
            "Run these operator-only commands, then rerun the same production deploy:",
            f"  {PRODUCTION_COMPOSE} run --rm backend python scripts/seed_admin.py",
            f"  {PRODUCTION_COMPOSE} run --rm backend python scripts/provision_admin_totp.py --acknowledge-production-mfa-bootstrap",
            "The TOTP secret and current code are prompted without echo and are never accepted as CLI arguments.",
        ]
    )


def main() -> int:
    db = SessionLocal()
    try:
        result = inspect_admin_mfa(db)
    finally:
        db.close()

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if not result["active_admins"]:
        print(_first_admin_bootstrap_instructions())
        return 2
    if result["missing_mfa"]:
        print(
            "Production administrator MFA is not enabled for: "
            + ", ".join(result["missing_mfa"])
        )
        print(
            "If no active administrator has MFA yet, use the one-time offline bootstrap for exactly one existing admin. "
            "Once an active MFA administrator exists, configure remaining administrators through the authenticated admin security API."
        )
        print(
            f"  {PRODUCTION_COMPOSE} run --rm backend python scripts/provision_admin_totp.py --acknowledge-production-mfa-bootstrap"
        )
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
