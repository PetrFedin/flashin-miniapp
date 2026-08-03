#!/usr/bin/env python3
"""Prove stale webhook workers cannot finish or mutate a reclaimed outbox row."""

from __future__ import annotations

import json
import sys
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import engine, utcnow_naive
from backend.jobs.outbox_jobs import (
    _claim_outbox,
    _finish_outbox,
    _renew_outbox_lease,
)
from backend.models import WebhookOutbox


def _state(db: Session, row_id: int) -> dict:
    row = db.execute(
        text(
            """
            SELECT
                id,
                destination,
                event_type,
                status,
                attempts,
                last_error,
                next_attempt_at,
                lease_token
            FROM webhook_outbox
            WHERE id = :row_id
            """
        ),
        {"row_id": row_id},
    ).mappings().one()
    return dict(row)


def _make_due(db: Session, row_id: int) -> None:
    db.execute(
        text(
            """
            UPDATE webhook_outbox
            SET next_attempt_at = :due_at
            WHERE id = :row_id
            """
        ),
        {
            "row_id": row_id,
            "due_at": utcnow_naive() - timedelta(seconds=1),
        },
    )
    db.commit()


def main() -> int:
    token = uuid.uuid4().hex[:20]
    connection = engine.connect()
    outer_transaction = connection.begin()
    db = Session(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    try:
        row = WebhookOutbox(
            destination=f"https://hooks.example.test/{token}",
            event_type=f"lease.smoke.{token}",
            payload=json.dumps({"token": token}, separators=(",", ":")),
            status="pending",
            attempts=0,
            last_error="",
            next_attempt_at=utcnow_naive(),
        )
        db.add(row)
        db.commit()
        row_id = row.id

        first_claim = _claim_outbox(db, limit=1)
        assert len(first_claim) == 1
        assert first_claim[0]["id"] == row_id
        first_token = first_claim[0]["lease_token"]
        assert isinstance(first_token, str) and len(first_token) == 32

        first_state = _state(db, row_id)
        assert first_state["status"] == "processing"
        assert first_state["attempts"] == 0
        assert first_state["lease_token"] == first_token
        assert first_state["next_attempt_at"] is not None

        assert _renew_outbox_lease(db, row_id, first_token) is True
        renewed_state = _state(db, row_id)
        assert renewed_state["lease_token"] == first_token
        assert renewed_state["status"] == "processing"

        _make_due(db, row_id)
        second_claim = _claim_outbox(db, limit=1)
        assert len(second_claim) == 1
        assert second_claim[0]["id"] == row_id
        second_token = second_claim[0]["lease_token"]
        assert second_token != first_token

        assert _renew_outbox_lease(db, row_id, first_token) is False
        assert (
            _finish_outbox(
                db,
                row_id,
                first_token,
                success=False,
                error="stale worker failure must be ignored",
            )
            is False
        )
        assert (
            _finish_outbox(
                db,
                row_id,
                first_token,
                success=True,
                destination="https://stale.example.test/ignored",
            )
            is False
        )

        after_stale_worker = _state(db, row_id)
        assert after_stale_worker["status"] == "processing"
        assert after_stale_worker["attempts"] == 0
        assert after_stale_worker["last_error"] == ""
        assert after_stale_worker["lease_token"] == second_token
        assert after_stale_worker["destination"].endswith(token)

        assert (
            _finish_outbox(
                db,
                row_id,
                second_token,
                success=False,
                error="active worker transient failure",
            )
            is True
        )
        retry_state = _state(db, row_id)
        assert retry_state["status"] == "pending"
        assert retry_state["attempts"] == 1
        assert retry_state["last_error"] == "active worker transient failure"
        assert retry_state["lease_token"] is None
        assert retry_state["next_attempt_at"] is not None

        _make_due(db, row_id)
        third_claim = _claim_outbox(db, limit=1)
        assert len(third_claim) == 1
        assert third_claim[0]["id"] == row_id
        third_token = third_claim[0]["lease_token"]
        assert third_token not in {first_token, second_token}

        normalized_destination = f"https://hooks.example.test/{token}/normalized"
        assert (
            _finish_outbox(
                db,
                row_id,
                third_token,
                success=True,
                destination=normalized_destination,
            )
            is True
        )
        final_state = _state(db, row_id)
        assert final_state["status"] == "sent"
        assert final_state["attempts"] == 1
        assert final_state["last_error"] == ""
        assert final_state["next_attempt_at"] is None
        assert final_state["lease_token"] is None
        assert final_state["destination"] == normalized_destination

        assert _renew_outbox_lease(db, row_id, third_token) is False
        assert _finish_outbox(db, row_id, third_token, success=True) is False
        assert _claim_outbox(db, limit=1) == []

        invalid_limits = (0, 501, True, 1.5)
        for invalid_limit in invalid_limits:
            try:
                _claim_outbox(db, limit=invalid_limit)  # type: ignore[arg-type]
            except ValueError:
                pass
            else:
                raise AssertionError(
                    f"Invalid outbox batch size was accepted: {invalid_limit!r}"
                )

        print(
            json.dumps(
                {
                    "status": "ok",
                    "row_id": row_id,
                    "lease_rotations": 3,
                    "stale_finish_rejected": True,
                    "attempts": final_state["attempts"],
                    "final_status": final_state["status"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        db.close()
        if outer_transaction.is_active:
            outer_transaction.rollback()
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
