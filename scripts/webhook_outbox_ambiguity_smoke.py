#!/usr/bin/env python3
"""Prove ambiguous webhook delivery is quarantined until explicit reconciliation."""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.api import outbox as outbox_api
from backend.database import engine, utcnow_naive
from backend.jobs import outbox_jobs
from backend.middleware.metrics import (
    WEBHOOK_OUTBOX_REVIEW_REQUIRED,
    WEBHOOK_OUTBOX_REVIEW_REQUIRED_TOTAL,
    collect_webhook_outbox_metrics,
)
from backend.models import AdminUser, AuditLog, WebhookDestination, WebhookOutbox
from backend.schemas import WebhookReviewActionIn


class _Response:
    def __init__(self, status_code: int, *, cancel_on_close: bool = False):
        self.status_code = status_code
        self.cancel_on_close = cancel_on_close

    async def aclose(self):
        if self.cancel_on_close:
            raise asyncio.CancelledError()
        return None


class _FakeClient:
    mode = "ambiguous"
    receiver_event_ids: list[str] = []

    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def build_request(self, method, url, *, content, headers):
        return httpx.Request(method, url, content=content, headers=headers)

    async def send(self, request, *, stream):
        assert stream is True
        event_id = request.headers["X-Flashin-Event-Id"]
        if self.mode == "connect_error":
            raise httpx.ConnectError("connection refused", request=request)

        self.receiver_event_ids.append(event_id)
        if self.mode == "ambiguous":
            raise httpx.ReadTimeout("response lost after receiver acceptance", request=request)
        if self.mode == "http400":
            return _Response(400)
        if self.mode == "success":
            return _Response(204)
        if self.mode == "success_close_cancel":
            return _Response(204, cancel_on_close=True)
        raise AssertionError(f"unsupported fake mode: {self.mode}")


def _state(db: Session, row_id: int) -> dict:
    row = db.execute(
        text(
            """
            SELECT id, status, attempts, last_error, next_attempt_at, lease_token
            FROM webhook_outbox
            WHERE id = :row_id
            """
        ),
        {"row_id": row_id},
    ).mappings().one()
    return dict(row)


def _new_outbox(db: Session, *, token: str, suffix: str) -> WebhookOutbox:
    row = WebhookOutbox(
        destination=f"https://hooks.example.test/{token}",
        event_type=f"ambiguity.{suffix}.{token}",
        payload=json.dumps({"token": token, "suffix": suffix}, separators=(",", ":")),
        status="pending",
        attempts=0,
        last_error="",
        next_attempt_at=utcnow_naive(),
    )
    db.add(row)
    db.commit()
    return row


def main() -> int:
    token = uuid.uuid4().hex[:16]
    connection = engine.connect()
    outer_transaction = connection.begin()
    db = Session(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    original_client = outbox_jobs.httpx.AsyncClient
    original_resolver = outbox_jobs.resolve_public_webhook_addresses
    original_permission = outbox_api.require_permission

    try:
        # Keep the smoke deterministic even if a developer database has old due rows.
        db.execute(
            text(
                """
                UPDATE webhook_outbox
                SET status = 'discarded', next_attempt_at = NULL
                WHERE status IN ('pending', 'processing')
                """
            )
        )
        db.commit()

        admin = AdminUser(
            email=f"webhook-review-{token}@flashin.test",
            password_hash="not-used-by-smoke",
            role="owner",
            active=True,
        )
        db.add(admin)
        destination = WebhookDestination(
            name=f"ambiguity-{token}",
            url=f"https://hooks.example.test/{token}",
            event_type="*",
            active=True,
            signing_secret="s" * 48,
        )
        db.add(destination)
        db.commit()

        outbox_jobs.httpx.AsyncClient = _FakeClient
        outbox_jobs.resolve_public_webhook_addresses = (
            lambda url: (url, ("198.51.100.10",))
        )
        outbox_api.require_permission = lambda *_args, **_kwargs: None
        _FakeClient.receiver_event_ids.clear()

        # 1) Receiver accepts but response is lost: exactly one send, then quarantine.
        ambiguous = _new_outbox(db, token=token, suffix="ambiguous")
        ambiguous_id = int(ambiguous.id)
        _FakeClient.mode = "ambiguous"
        assert asyncio.run(outbox_jobs.process_outbox(db)) == 0
        ambiguous_state = _state(db, ambiguous_id)
        assert ambiguous_state["status"] == "review_required"
        assert ambiguous_state["attempts"] == 1
        assert ambiguous_state["next_attempt_at"] is None
        assert ambiguous_state["lease_token"] is None
        assert ambiguous_state["last_error"].startswith(
            "classification=ambiguous_transport;"
        )
        assert _FakeClient.receiver_event_ids == [str(ambiguous_id)]

        # A normal worker pass cannot replay quarantined delivery.
        assert asyncio.run(outbox_jobs.process_outbox(db)) == 0
        assert _FakeClient.receiver_event_ids == [str(ambiguous_id)]

        assert collect_webhook_outbox_metrics(db) is True
        assert WEBHOOK_OUTBOX_REVIEW_REQUIRED_TOTAL._value.get() >= 1
        assert (
            WEBHOOK_OUTBOX_REVIEW_REQUIRED.labels(
                classification="ambiguous_transport"
            )._value.get()
            >= 1
        )

        # Explicit operator replay preserves the stable event identity.
        replayed = outbox_api.retry_review_outbox(
            ambiguous_id,
            WebhookReviewActionIn(
                event_id=ambiguous_id,
                reason_code="receiver_support_authorized_replay",
            ),
            admin=admin,
            db=db,
        )
        assert replayed["status"] == "pending"
        assert replayed["attempts"] == 1

        _FakeClient.mode = "success"
        assert asyncio.run(outbox_jobs.process_outbox(db)) == 1
        final_ambiguous = _state(db, ambiguous_id)
        assert final_ambiguous["status"] == "sent"
        assert final_ambiguous["attempts"] == 1
        assert _FakeClient.receiver_event_ids == [
            str(ambiguous_id),
            str(ambiguous_id),
        ]

        # 2) Once a 2xx status is received, cancellation during response close
        # cannot return the row to retryable processing.
        close_cancel = _new_outbox(db, token=token, suffix="close-cancel")
        close_cancel_id = int(close_cancel.id)
        sends_before_close_cancel = len(_FakeClient.receiver_event_ids)
        _FakeClient.mode = "success_close_cancel"
        try:
            asyncio.run(outbox_jobs.process_outbox(db))
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("response-close cancellation did not propagate")
        close_cancel_state = _state(db, close_cancel_id)
        assert close_cancel_state["status"] == "sent"
        assert close_cancel_state["next_attempt_at"] is None
        assert len(_FakeClient.receiver_event_ids) == sends_before_close_cancel + 1
        _FakeClient.mode = "success"
        assert asyncio.run(outbox_jobs.process_outbox(db)) == 0
        assert len(_FakeClient.receiver_event_ids) == sends_before_close_cancel + 1

        # 3) Connect failure is known pre-dispatch and remains safely retryable.
        connect = _new_outbox(db, token=token, suffix="connect")
        connect_id = int(connect.id)
        before_receiver_count = len(_FakeClient.receiver_event_ids)
        _FakeClient.mode = "connect_error"
        assert asyncio.run(outbox_jobs.process_outbox(db)) == 0
        connect_state = _state(db, connect_id)
        assert connect_state["status"] == "pending"
        assert connect_state["attempts"] == 1
        assert connect_state["last_error"].startswith(
            "classification=safe_retry_pre_dispatch;"
        )
        assert len(_FakeClient.receiver_event_ids) == before_receiver_count

        # 4) Permanent 4xx is terminal review, and reconciliation can mark sent
        # without creating another provider call.
        permanent = _new_outbox(db, token=token, suffix="http400")
        permanent_id = int(permanent.id)
        _FakeClient.mode = "http400"
        assert asyncio.run(outbox_jobs.process_outbox(db)) == 0
        permanent_state = _state(db, permanent_id)
        assert permanent_state["status"] == "review_required"
        assert permanent_state["last_error"].startswith(
            "classification=permanent_http;"
        )
        sends_before_mark = len(_FakeClient.receiver_event_ids)

        marked = outbox_api.mark_review_outbox_sent(
            permanent_id,
            WebhookReviewActionIn(
                event_id=permanent_id,
                reason_code="receiver_confirmed_processed",
            ),
            admin=admin,
            db=db,
        )
        assert marked["status"] == "sent"
        _FakeClient.mode = "success"
        assert asyncio.run(outbox_jobs.process_outbox(db)) == 0
        assert len(_FakeClient.receiver_event_ids) == sends_before_mark

        audit_actions = {
            row.action
            for row in db.query(AuditLog)
            .filter(AuditLog.entity_type == "webhook_outbox")
            .all()
        }
        assert "webhook_outbox.review_required" in audit_actions
        assert "webhook_outbox.review_replay" in audit_actions
        assert "webhook_outbox.review_mark_sent" in audit_actions

        print(
            json.dumps(
                {
                    "status": "ok",
                    "ambiguous_event_id": ambiguous_id,
                    "ambiguous_send_count": 2,
                    "blind_replay_blocked": True,
                    "stable_event_identity": True,
                    "response_close_cancellation_safe": True,
                    "connect_failure_retryable": True,
                    "permanent_http_quarantined": True,
                    "operator_mark_sent_without_resend": True,
                    "review_metrics_visible": True,
                    "structured_audit_visible": True,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        outbox_jobs.httpx.AsyncClient = original_client
        outbox_jobs.resolve_public_webhook_addresses = original_resolver
        outbox_api.require_permission = original_permission
        db.close()
        if outer_transaction.is_active:
            outer_transaction.rollback()
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
