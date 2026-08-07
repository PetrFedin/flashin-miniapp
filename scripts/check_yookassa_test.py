#!/usr/bin/env python3
"""Create one idempotent 1 RUB YooKassa pilot probe payment with bounded output."""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

ALLOWED_STATUSES = {"pending", "waiting_for_capture", "succeeded"}


def build_idempotence_key(shop_id: str, release_commit: str, return_url: str = "") -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"flashin:pilot-provider-probe:yookassa:{shop_id}:{release_commit}:{return_url}",
        )
    )


def validate_response(body: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if not str(body.get("id", "")).strip():
        errors.append("payment id is missing")
    status = str(body.get("status", ""))
    if status not in ALLOWED_STATUSES:
        errors.append("payment status is invalid")
    amount = body.get("amount")
    if not isinstance(amount, Mapping):
        errors.append("payment amount is missing")
    else:
        try:
            value = Decimal(str(amount.get("value", "")))
        except (InvalidOperation, ValueError):
            errors.append("payment amount is invalid")
        else:
            if value != Decimal("1.00"):
                errors.append("payment amount mismatch")
        if amount.get("currency") != "RUB":
            errors.append("payment currency mismatch")
    if status == "pending":
        confirmation = body.get("confirmation")
        if not isinstance(confirmation, Mapping) or not confirmation.get("confirmation_url"):
            errors.append("pending payment confirmation URL is missing")
    return errors


def main() -> int:
    shop_id = str(os.getenv("YOOKASSA_SHOP_ID", "")).strip()
    secret = str(os.getenv("YOOKASSA_SECRET_KEY", "")).strip()
    return_url = os.getenv(
        "YOOKASSA_RETURN_URL",
        "https://mini.flashin.store/payment-result",
    )
    release_commit = str(os.getenv("FLASHIN_RELEASE_GIT_COMMIT", "manual")).strip() or "manual"
    idempotence_key = str(os.getenv("FLASHIN_YOOKASSA_IDEMPOTENCE_KEY", "")).strip()

    if not shop_id or not secret:
        print(json.dumps({"ok": False, "error": "YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY are required"}))
        return 1
    if not idempotence_key:
        idempotence_key = build_idempotence_key(shop_id, release_commit, return_url)

    payload = {
        "amount": {"value": "1.00", "currency": "RUB"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": return_url},
        "description": "FLASHIN pilot provider probe",
        "metadata": {
            "pilot_probe": "true",
            "release_commit": release_commit[:40],
        },
    }
    data = json.dumps(payload).encode("utf-8")
    auth = base64.b64encode(f"{shop_id}:{secret}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(
        "https://api.yookassa.ru/v3/payments",
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Idempotence-Key": idempotence_key,
            "Authorization": f"Basic {auth}",
            "User-Agent": "flashin-pilot-provider-probe/3.0",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        print(json.dumps({"ok": False, "error": f"YooKassa HTTP {exc.code}"}, ensure_ascii=False))
        return 1
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(json.dumps({"ok": False, "error": exc.__class__.__name__}, ensure_ascii=False))
        return 1
    except Exception as exc:
        print(json.dumps({"ok": False, "error": exc.__class__.__name__}, ensure_ascii=False))
        return 1

    try:
        body = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        print(json.dumps({"ok": False, "error": "invalid provider JSON"}))
        return 1
    if not isinstance(body, Mapping):
        print(json.dumps({"ok": False, "error": "invalid provider object"}))
        return 1

    errors = validate_response(body)
    safe = {
        "ok": not errors,
        "provider": "yookassa",
        "status": body.get("status") if body.get("status") in ALLOWED_STATUSES else "invalid",
        "amount": "1.00" if isinstance(body.get("amount"), Mapping) and body.get("amount", {}).get("value") == "1.00" else "invalid",
        "currency": "RUB" if isinstance(body.get("amount"), Mapping) and body.get("amount", {}).get("currency") == "RUB" else "invalid",
        "confirmation_required": body.get("status") == "pending",
        "idempotence_scope": "current-release-and-return-url",
        "errors": errors,
    }
    print(json.dumps(safe, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
