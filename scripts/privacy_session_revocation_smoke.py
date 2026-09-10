#!/usr/bin/env python3
"""Prove privacy deletion revokes every durable customer session atomically."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from backend.api.privacy import admin_process_privacy_request
from backend.customer_auth_models import CustomerSession
from backend.database import SessionLocal, engine
from backend.models import AdminUser, Customer, PrivacyRequest
from backend.security import get_current_customer
from backend.services.customer_auth_security import issue_customer_session_token


def main() -> int:
    if engine.dialect.name != "postgresql":
        raise RuntimeError("privacy session revocation smoke requires PostgreSQL")

    token = uuid.uuid4().hex[:16]
    with SessionLocal() as db:
        admin = AdminUser(
            email=f"privacy-owner-{token}@test.local",
            password_hash="not-used-by-direct-smoke",
            role="owner",
            active=True,
        )
        customer = Customer(
            telegram_id=f"privacy-auth-{token}",
            first_name="Privacy",
            last_name="Auth",
        )
        db.add_all([admin, customer])
        db.flush()
        customer_id = int(customer.id)
        bearer_token = issue_customer_session_token(db, customer_id)
        privacy_request = PrivacyRequest(
            customer_id=customer_id,
            request_type="delete",
            status="requested",
        )
        db.add(privacy_request)
        db.commit()
        request_id = int(privacy_request.id)

        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=bearer_token)
        assert get_current_customer(credentials=credentials, db=db).id == customer_id

        result = admin_process_privacy_request(request_id, admin=admin, db=db)
        assert result["ok"] is True
        assert result["idempotent"] is False

        db.expire_all()
        session_rows = (
            db.query(CustomerSession)
            .filter(CustomerSession.customer_id == customer_id)
            .all()
        )
        assert len(session_rows) == 1
        assert session_rows[0].revoked_at is not None

        deleted_customer = db.query(Customer).filter(Customer.id == customer_id).one()
        assert deleted_customer.telegram_id.startswith(f"deleted:{customer_id}:")
        assert deleted_customer.first_name == ""
        assert deleted_customer.last_name == ""

        try:
            get_current_customer(credentials=credentials, db=db)
        except HTTPException as exc:
            assert exc.status_code == 401, exc
        else:
            raise AssertionError("privacy-deleted customer bearer remained usable")

        print(
            {
                "status": "ok",
                "request_id": request_id,
                "sessions": len(session_rows),
                "all_sessions_revoked": True,
                "bearer_rejected_after_delete": True,
                "customer_anonymized": True,
            }
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
