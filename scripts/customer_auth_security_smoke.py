#!/usr/bin/env python3
"""Prove one-time Telegram bootstrap and revocable customer sessions on PostgreSQL."""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from backend.api.auth import logout, telegram_auth
from backend.customer_auth_models import CustomerSession, TelegramAuthConsumption
from backend.database import SessionLocal, engine
from backend.models import Customer
from backend.schemas import TelegramAuthIn
from backend.security import create_access_token, get_current_customer, verify_telegram_init_data
from backend.services.customer_auth_security import (
    consume_telegram_assertion,
    is_customer_session_active,
    issue_customer_session_token,
    revoke_all_customer_sessions,
)

BOT_TOKEN = "test-token"


def _signed_init_data(*, user_id: int, query_id: str, auth_date: int) -> str:
    fields = {
        "auth_date": str(auth_date),
        "query_id": query_id,
        "user": json.dumps(
            {
                "id": user_id,
                "username": f"auth_{user_id}",
                "first_name": "Auth",
                "last_name": "Smoke",
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
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


def _expect_unauthorized(fn) -> str:
    try:
        fn()
    except HTTPException as exc:
        assert exc.status_code == 401, exc
        return str(exc.detail)
    raise AssertionError("Expected HTTP 401")


def _bootstrap_once(token: str) -> dict:
    raw = _signed_init_data(
        user_id=8_000_000_000 + int(token[:6], 16),
        query_id=f"AAH-{token}-bootstrap",
        auth_date=int(time.time()),
    )
    with SessionLocal() as db:
        result = telegram_auth(TelegramAuthIn(init_data=raw), db=db)
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=result.access_token)
        customer = get_current_customer(credentials=credentials, db=db)
        customer_id = int(customer.id)

    def replay() -> None:
        with SessionLocal() as replay_db:
            telegram_auth(TelegramAuthIn(init_data=raw), db=replay_db)

    replay_detail = _expect_unauthorized(replay)

    with SessionLocal() as db:
        consumption = (
            db.query(TelegramAuthConsumption)
            .filter(TelegramAuthConsumption.query_id_digest.is_not(None))
            .order_by(TelegramAuthConsumption.id.desc())
            .first()
        )
        assert consumption is not None
        assert raw not in consumption.assertion_digest
        assert raw not in str(consumption.query_id_digest or "")
        assert len(consumption.assertion_digest) == 64
        session = (
            db.query(CustomerSession)
            .filter(CustomerSession.customer_id == customer_id)
            .order_by(CustomerSession.id.desc())
            .first()
        )
        assert session is not None
        assert result.access_token not in session.token_id_hash
        assert len(session.token_id_hash) == 64

        unknown_token = create_access_token(customer_id)
        unknown_credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=unknown_token)
        unknown_detail = _expect_unauthorized(
            lambda: get_current_customer(credentials=unknown_credentials, db=db)
        )

    return {
        "customer_id": customer_id,
        "replay_rejected": replay_detail,
        "unknown_session_rejected": unknown_detail,
        "raw_credentials_persisted": False,
    }


def _concurrent_replay(token: str, contenders: int = 10) -> dict:
    raw = _signed_init_data(
        user_id=8_100_000_000 + int(token[:6], 16),
        query_id=f"AAH-{token}-race",
        auth_date=int(time.time()),
    )
    parsed = verify_telegram_init_data(raw)

    def attempt(_: int) -> str:
        try:
            consume_telegram_assertion(parsed)
            return "accepted"
        except HTTPException as exc:
            assert exc.status_code == 401, exc
            return "rejected"

    with ThreadPoolExecutor(max_workers=contenders) as pool:
        outcomes = list(pool.map(attempt, range(contenders)))
    assert outcomes.count("accepted") == 1, outcomes
    assert outcomes.count("rejected") == contenders - 1, outcomes
    return {
        "contenders": contenders,
        "accepted": outcomes.count("accepted"),
        "rejected": outcomes.count("rejected"),
    }


def _session_revocation(token: str) -> dict:
    telegram_id = f"8200{int(token[:8], 16)}"
    with SessionLocal() as db:
        customer = Customer(telegram_id=telegram_id, first_name="Session")
        db.add(customer)
        db.flush()
        customer_id = int(customer.id)
        token_one = issue_customer_session_token(db, customer_id)
        token_two = issue_customer_session_token(db, customer_id)
        db.commit()

        from backend.security import get_customer_token_payload

        jti_one = str(get_customer_token_payload(token_one)["jti"])
        jti_two = str(get_customer_token_payload(token_two)["jti"])
        assert is_customer_session_active(db, customer_id, jti_one)
        assert is_customer_session_active(db, customer_id, jti_two)

        credentials_one = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token_one)
        credentials_two = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token_two)
        assert get_current_customer(credentials=credentials_one, db=db).id == customer_id
        assert get_current_customer(credentials=credentials_two, db=db).id == customer_id

        first_logout = logout(credentials=credentials_one, db=db)
        second_logout = logout(credentials=credentials_one, db=db)
        assert first_logout == {"ok": True}
        assert second_logout == {"ok": True}
        assert not is_customer_session_active(db, customer_id, jti_one)
        _expect_unauthorized(lambda: get_current_customer(credentials=credentials_one, db=db))
        assert get_current_customer(credentials=credentials_two, db=db).id == customer_id

        revoked = revoke_all_customer_sessions(db, customer_id)
        db.commit()
        assert revoked == 1
        assert not is_customer_session_active(db, customer_id, jti_two)
        _expect_unauthorized(lambda: get_current_customer(credentials=credentials_two, db=db))

    return {"logout_retry_idempotent": True, "revoke_all_remaining": revoked}


def _bootstrap_time_window(token: str) -> dict:
    expired = _signed_init_data(
        user_id=8_300_000_000 + int(token[:6], 16),
        query_id=f"AAH-{token}-expired",
        auth_date=int(time.time()) - 11 * 60,
    )
    expired_detail = _expect_unauthorized(lambda: verify_telegram_init_data(expired))

    future = _signed_init_data(
        user_id=8_400_000_000 + int(token[:6], 16),
        query_id=f"AAH-{token}-future",
        auth_date=int(time.time()) + 6 * 60,
    )
    future_detail = _expect_unauthorized(lambda: verify_telegram_init_data(future))
    return {"expired": expired_detail, "future": future_detail}


def main() -> int:
    if engine.dialect.name != "postgresql":
        raise RuntimeError("customer auth security smoke requires PostgreSQL")
    token = uuid.uuid4().hex[:16]
    result = {
        "status": "ok",
        "bootstrap": _bootstrap_once(token),
        "concurrent_replay": _concurrent_replay(token),
        "sessions": _session_revocation(token),
        "time_window": _bootstrap_time_window(token),
    }
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
