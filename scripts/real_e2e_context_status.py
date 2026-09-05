#!/usr/bin/env python3
"""Inspect the private real-provider E2E context without exposing credentials.

The side-effectful real-order runner atomically claims the context before the
first cart mutation and then advances it through ``checkout_intent`` and
``order_created`` to ``payment_created``. This command is an operator recovery
aid: provisional phases are intentionally non-zero/NO-GO so a second real-payment
run is never the default response to an interrupted attempt.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTEXT = ROOT / "docs/pilot/evidence/real_order_e2e_context.json"
VALID_PHASES = frozenset(
    {"preflight_intent", "checkout_intent", "order_created", "payment_created"}
)


def _positive_int(payload: Mapping[str, Any], key: str) -> int | None:
    raw = payload.get(key)
    if isinstance(raw, bool):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def inspect_context(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {
            "ok": False,
            "phase": None,
            "requires_investigation": False,
            "errors": [f"real E2E context file is missing: {path}"],
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "phase": None,
            "requires_investigation": True,
            "errors": [f"real E2E context file is invalid: {exc.__class__.__name__}"],
        }
    if not isinstance(payload, Mapping):
        return {
            "ok": False,
            "phase": None,
            "requires_investigation": True,
            "errors": ["real E2E context must be a JSON object"],
        }

    errors: list[str] = []
    if payload.get("schema_version") != 1:
        errors.append("real E2E context schema is invalid")
    if payload.get("kind") != "flashin_real_order_e2e_context":
        errors.append("real E2E context kind is invalid")
    phase = str(payload.get("phase") or "").strip()
    if phase not in VALID_PHASES:
        errors.append("real E2E context phase is invalid")
    api_base = str(payload.get("api_base") or "").strip().rstrip("/")
    if not api_base:
        errors.append("real E2E context api_base is missing")
    if payload.get("provider") != "yookassa":
        errors.append("real E2E context provider is invalid")
    variant_id = _positive_int(payload, "variant_id")
    if variant_id is None:
        errors.append("real E2E context variant_id is invalid")
    baseline_stock = _positive_int(payload, "baseline_stock_qty")
    if baseline_stock is None:
        errors.append("real E2E context baseline_stock_qty is invalid")
    try:
        baseline_reserved = int(payload.get("baseline_reserved_qty"))
    except (TypeError, ValueError):
        baseline_reserved = -1
    if baseline_reserved != 0:
        errors.append("real E2E context baseline reservation must be zero")

    order_id = _positive_int(payload, "order_id")
    provider_payment_id = str(payload.get("provider_payment_id") or "").strip()
    if phase in {"order_created", "payment_created"}:
        if order_id is None:
            errors.append("real E2E context order_id is missing after checkout")
        elif str(payload.get("subject_id") or "").strip() != f"order:{order_id}":
            errors.append("real E2E context subject_id does not match order_id")
    if phase == "payment_created" and not provider_payment_id:
        errors.append("real E2E context provider_payment_id is missing after payment creation")

    provisional = phase in {"preflight_intent", "checkout_intent", "order_created"}
    if phase == "preflight_intent":
        recovery = (
            "Do not rerun real-order E2E. The exclusive real-payment slot was claimed before "
            "cart mutation. Investigate whether the prior process changed the controlled cart or "
            "advanced to checkout/order creation before archiving this marker."
        )
    elif phase == "checkout_intent":
        recovery = (
            "Do not rerun real-order E2E. Investigate whether checkout was accepted using "
            "the pilot customer/order records and the recorded context timestamp/idempotency key."
        )
    elif phase == "order_created":
        recovery = (
            "Do not rerun real-order E2E. Inspect the recorded order and authoritative YooKassa "
            "payment state before deciding whether the attempt can be safely completed or archived."
        )
    elif phase == "payment_created":
        recovery = (
            "Payment creation is durably recorded. Continue only with the same controlled order "
            "through provider callback, fulfillment, refund, terminal verification and evidence."
        )
    else:
        recovery = "Do not rerun real-order E2E until the invalid context has been investigated."

    return {
        "ok": not errors and phase == "payment_created",
        "phase": phase or None,
        "requires_investigation": provisional or bool(errors),
        "api_base": api_base or None,
        "order_id": order_id,
        "variant_id": variant_id,
        "provider": "yookassa" if payload.get("provider") == "yookassa" else None,
        "provider_payment_id": provider_payment_id or None,
        "recovery_action": recovery,
        "errors": list(dict.fromkeys(errors)),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", type=Path, default=DEFAULT_CONTEXT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = inspect_context(args.context)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())