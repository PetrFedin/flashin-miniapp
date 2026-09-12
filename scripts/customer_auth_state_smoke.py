#!/usr/bin/env python3
"""PostgreSQL proof for Telegram replay safety and customer session revocation."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode

from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.api.auth import telegram_auth
from backend.api.privacy import admin_process_privacy_request
from backend.auth_models import CustomerAuthEvent, CustomerSession, TelegramAuthAssertion
from backend.database import SessionLocal, utcnow_naive
from backend.main import app
from backend.models import AdminUser, Customer, PrivacyRequest
from backend.schemas import TelegramAuthIn
from backend.security import verify_telegram_init_data
from backend.services.customer_auth import claim_telegram_assertion, revoke_customer_sessions


BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]


def _telegram_init_data(
    *,
    telegram_id: int,
    query_id: str,
    auth_date: int | None = None,
) -> str:
    user = json.dumps(
        {
            "id": telegram_id,
            "first_name": "Auth Smoke",
            "username": f"auth_smoke_{telegram_id}",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    fields = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": query_id,
        "user": user,
    }
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret_key = hmac.new(
        key=b"WebAppData",
        msg=BOT_TOKEN.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    fields["hash"] = hmac.new(
        key=secret_key,
        msg=data_check_string.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()
    return urlencode(fields)


def _tamper(init_data: str) -> str:
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    user = json.loads(pairs["user"])
    user["first_name"] = "Tampered"
    pairs["user"] = json.dumps(user, separators=(",", ":"))
    return urlencode(pairs)


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _direct_auth_status(init_data: str) -> int:
    db = SessionLocal()
    try:
        telegram_auth(TelegramAuthIn(init_data=init_data), db=db)
        return 200
    except HTTPException as exc:
        return int(exc.status_code)
    finally:
        db.close()


def _assert_revoked_route_barrier(client: TestClient, token: str) -> None:
    headers = _auth_header(token)
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert client.get("/api/orders", headers=headers).status_code == 401
    assert client.get("/api/privacy/requests", headers=headers).status_code == 401
    checkout = client.post(
        "/api/orders/checkout",
        headers={**headers, "Idempotency-Key": f"auth-smoke-{secrets.token_hex(8)}"},
        json={
            "name": "Auth Smoke",
            "phone": "+79990000000",
            "delivery_type": "pickup",
            "address": "",
            "comment": "",
        },
    )
    assert checkout.status_code == 401, checkout.text


def main() -> None:
    seed = int(time.time() * 1000) % 9_000_000_000
    primary_user = 8_000_000_000 + seed

    with TestClient(app) as client:
        # Signature and time boundaries remain fail-closed.
        expired = _telegram_init_data(
            telegram_id=primary_user,
            query_id=f"expired-{secrets.token_hex(8)}",
            auth_date=int(time.time()) - (15 * 60 + 2),
        )
        assert client.post("/api/auth/telegram", json={"init_data": expired}).status_code == 401
        future = _telegram_init_data(
            telegram_id=primary_user,
            query_id=f"future-{secrets.token_hex(8)}",
            auth_date=int(time.time()) + (5 * 60 + 2),
        )
        assert client.post("/api/auth/telegram", json={"init_data": future}).status_code == 401
        valid_for_tamper = _telegram_init_data(
            telegram_id=primary_user,
            query_id=f"tamper-{secrets.token_hex(8)}",
        )
        assert (
            client.post(
                "/api/auth/telegram",
                json={"init_data": _tamper(valid_for_tamper)},
            ).status_code
            == 401
        )

        # One signed bootstrap can mint exactly one persisted customer session.
        query_id = f"first-{secrets.token_hex(12)}"
        first_init = _telegram_init_data(
            telegram_id=primary_user,
            query_id=query_id,
        )
        first = client.post("/api/auth/telegram", json={"init_data": first_init})
        assert first.status_code == 200, first.text
        first_token = first.json()["access_token"]
        assert client.get("/api/auth/me", headers=_auth_header(first_token)).status_code == 200
        replay = client.post("/api/auth/telegram", json={"init_data": first_init})
        assert replay.status_code == 401, replay.text

        # Concurrent identical submissions are serialized by the durable unique
        # assertion claim: exactly one success, exactly one replay rejection.
        concurrent_user = primary_user + 1
        concurrent_init = _telegram_init_data(
            telegram_id=concurrent_user,
            query_id=f"concurrent-{secrets.token_hex(12)}",
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = sorted(pool.map(_direct_auth_status, [concurrent_init, concurrent_init]))
        assert statuses == [200, 401], statuses

        # Logout is idempotent for the same already-known session and instantly
        # blocks every protected customer contour that shares the auth dependency.
        logout = client.post("/api/auth/logout", headers=_auth_header(first_token))
        assert logout.status_code == 200 and logout.json()["idempotent"] is False
        logout_again = client.post("/api/auth/logout", headers=_auth_header(first_token))
        assert logout_again.status_code == 200 and logout_again.json()["idempotent"] is True
        _assert_revoked_route_barrier(client, first_token)

        # Two fresh bootstrap assertions for one Telegram customer create two
        # separately revocable sessions without persisting either raw bearer.
        session_user = primary_user + 2
        tokens: list[str] = []
        for index in range(2):
            init_data = _telegram_init_data(
                telegram_id=session_user,
                query_id=f"session-{index}-{secrets.token_hex(12)}",
            )
            response = client.post("/api/auth/telegram", json={"init_data": init_data})
            assert response.status_code == 200, response.text
            tokens.append(response.json()["access_token"])

        listed = client.get("/api/auth/sessions", headers=_auth_header(tokens[0]))
        assert listed.status_code == 200, listed.text
        sessions = listed.json()
        current = next(row for row in sessions if row["current"])
        target = next(row for row in sessions if not row["current"] and not row["revoked"])
        revoke_one = client.post(
            f"/api/auth/sessions/{target['id']}/revoke",
            headers=_auth_header(tokens[0]),
        )
        assert revoke_one.status_code == 200 and revoke_one.json()["idempotent"] is False
        revoke_one_again = client.post(
            f"/api/auth/sessions/{target['id']}/revoke",
            headers=_auth_header(tokens[0]),
        )
        assert revoke_one_again.status_code == 200 and revoke_one_again.json()["idempotent"] is True
        assert client.get("/api/auth/me", headers=_auth_header(tokens[1])).status_code == 401
        assert client.get("/api/auth/me", headers=_auth_header(tokens[0])).status_code == 200

        revoke_all = client.post(
            "/api/auth/sessions/revoke-all",
            headers=_auth_header(tokens[0]),
        )
        assert revoke_all.status_code == 200 and revoke_all.json()["revoked"] >= 1
        _assert_revoked_route_barrier(client, tokens[0])

        # Privacy deletion is a single transaction that revokes active sessions
        # before anonymizing the customer.
        privacy_user = primary_user + 3
        privacy_tokens: list[str] = []
        for index in range(2):
            init_data = _telegram_init_data(
                telegram_id=privacy_user,
                query_id=f"privacy-{index}-{secrets.token_hex(12)}",
            )
            response = client.post("/api/auth/telegram", json={"init_data": init_data})
            assert response.status_code == 200, response.text
            privacy_tokens.append(response.json()["access_token"])

        db = SessionLocal()
        try:
            customer = db.query(Customer).filter(Customer.telegram_id == str(privacy_user)).one()
            admin = AdminUser(
                email=f"auth-smoke-owner-{secrets.token_hex(6)}@example.test",
                password_hash="not-used-by-smoke",
                role="owner",
                active=True,
            )
            db.add(admin)
            db.flush()
            request = PrivacyRequest(
                customer_id=customer.id,
                request_type="delete",
                status="requested",
            )
            db.add(request)
            db.commit()
            result = admin_process_privacy_request(request.id, admin=admin, db=db)
            assert result["ok"] is True
            assert result["result"]["sessions_revoked"] == 2
        finally:
            db.close()
        for token in privacy_tokens:
            _assert_revoked_route_barrier(client, token)

        # Persistence evidence contains only fingerprints/metadata, never raw
        # initData, query_id, Telegram signature or bearer token material.
        db = SessionLocal()
        try:
            assertion = (
                db.query(TelegramAuthAssertion)
                .order_by(TelegramAuthAssertion.id.asc())
                .first()
            )
            assert assertion is not None
            assert len(assertion.assertion_hash) == 64
            assert len(assertion.payload_hash) == 64
            assert query_id not in assertion.assertion_hash
            assert first_init not in assertion.assertion_hash

            all_sessions = db.query(CustomerSession).all()
            all_events = db.query(CustomerAuthEvent).all()
            persisted = "\n".join(
                [
                    row.session_id_hash + row.jti_hash + row.revoke_reason
                    for row in all_sessions
                ]
                + [row.event_type for row in all_events]
            )
            for token in [first_token, *tokens, *privacy_tokens]:
                assert token not in persisted

            # Replay metadata has explicit bounded retention and stale rows are
            # purged as part of the next assertion claim.
            stale = TelegramAuthAssertion(
                assertion_hash=hashlib.sha256(secrets.token_bytes(16)).hexdigest(),
                payload_hash=hashlib.sha256(secrets.token_bytes(16)).hexdigest(),
                auth_date=int(time.time()) - 86_400,
                consumed_at=utcnow_naive() - timedelta(days=2),
                retain_until=utcnow_naive() - timedelta(minutes=1),
            )
            db.add(stale)
            db.commit()
            stale_id = stale.id

            cleanup_init = _telegram_init_data(
                telegram_id=primary_user + 4,
                query_id=f"cleanup-{secrets.token_hex(12)}",
            )
            parsed = verify_telegram_init_data(cleanup_init)
            claim_telegram_assertion(db, parsed)
            db.commit()
            assert db.query(TelegramAuthAssertion).filter_by(id=stale_id).first() is None

            # The service-level revoke-all operation is idempotent even after an
            # HTTP revoke-all has already completed.
            session_customer = db.query(Customer).filter(Customer.telegram_id == str(session_user)).one()
            assert (
                revoke_customer_sessions(
                    db,
                    customer_id=session_customer.id,
                    reason="auth_smoke_repeat_revoke_all",
                )
                == 0
            )
            db.rollback()
        finally:
            db.close()

    print("customer auth replay/revocation smoke: PASS")


if __name__ == "__main__":
    main()
