#!/usr/bin/env python3
"""Fail-closed control plane for the first 20 FLASHIN pilot orders."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping

from pilot_control_binding import build_admission_binding, require_admission_binding
from script_time import utc_timestamp

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 2
DEFAULT_STATE_PATH = Path("docs/pilot/live_pilot_state.json")
DEFAULT_MANIFEST_PATH = Path("docs/pilot/pilot_admission_manifest.json")
ALLOWED_RESULTS = {"todo", "running", "pass", "fail", "blocked"}
MONEY_TOLERANCE = Decimal("0.01")

# number, wave, title, critical, expected order status, flags, must verify, optional stock delta
# flags: o=order, p=payment, s=stock, r=refund, i=repeated webhook/idempotency
_SCENARIO_ROWS = (
    (1, 1, "Один SKU, самовывоз, успешная оплата", True, "paid", "ops", "заказ, платёж, остаток, уведомление", None),
    (2, 1, "Один SKU, курьер, успешная оплата", True, "paid", "ops", "стоимость доставки и единичное списание", None),
    (3, 1, "Повторный обычный заказ без вмешательства разработчика", True, "paid", "ops", "полный сквозной путь оператором", None),
    (4, 2, "Заказ с промокодом", True, "paid", "ops", "скидка до создания платежа", None),
    (5, 2, "Заказ со списанием бонусов", True, "paid", "ops", "баланс бонусов и итог заказа", None),
    (6, 2, "Повторная доставка payment webhook", True, "paid", "opsi", "два webhook, один доменный эффект", None),
    (7, 2, "Отмена неоплаченного заказа", True, "canceled", "os", "резерв освобождён, списания нет", 0),
    (8, 3, "Заказ из нескольких позиций", True, "paid", "ops", "сумма строк и совокупное списание", None),
    (9, 3, "Пограничный остаток", True, "paid", "ops", "остаток не уходит ниже нуля", None),
    (10, 3, "Поздний платёж после истечения резерва", True, "review", "ops", "нет скрытого списания, создан review case", 0),
    (11, 3, "Support ticket после заказа", False, "paid", "ops", "тикет связан с order ID", None),
    (12, 3, "Fulfillment picking", True, "paid", "ops", "создана одна задача комплектации", None),
    (13, 4, "Полный возврат", True, "refunded", "opsr", "деньги, товар и бонусы возвращены один раз", None),
    (14, 4, "Частичный возврат", True, "partially_refunded", "opsr", "возвращена только подтверждённая часть", None),
    (15, 4, "Повторный refund callback", True, "refunded", "opsri", "два callback, один возврат и одно оприходование", None),
    (16, 4, "Аномалия платежа на ручном review", True, "review", "ops", "оператор видит причину и не проводит заказ автоматически", 0),
    (17, 4, "MoySklad конфликт остатка", True, "review", "", "конфликт видим и имеет владельца", None),
    (18, 4, "Восстановление failed BusinessEvent", True, "paid", "ops", "ошибка диагностирована, replay контролируемый, эффект один", None),
    (19, 4, "Медленная мобильная сеть", False, "paid", "ops", "повтор нажатия не создаёт дубль заказа или платежа", None),
    (20, 4, "Обычный клиентский поток оператором по SOP", True, "paid", "ops", "нет ручного вмешательства разработчика", None),
)


def _build_scenario(row: tuple[Any, ...]) -> dict[str, Any]:
    number, wave, title, critical, expected_status, flags, must_verify, stock_delta = row
    scenario = {
        "number": number,
        "wave": wave,
        "title": title,
        "critical": critical,
        "expected_order_status": expected_status,
        "requires_order": "o" in flags,
        "requires_payment": "p" in flags,
        "requires_stock": "s" in flags,
        "requires_refund": "r" in flags,
        "requires_webhook_idempotency": "i" in flags,
        "must_verify": must_verify,
    }
    if stock_delta is not None:
        scenario["expected_stock_delta"] = stock_delta
    return scenario


SCENARIOS = tuple(_build_scenario(row) for row in _SCENARIO_ROWS)
SCENARIO_BY_NUMBER = {scenario["number"]: scenario for scenario in SCENARIOS}


def _empty_record(scenario: dict[str, Any]) -> dict[str, Any]:
    record = {
        "number": scenario["number"],
        "wave": scenario["wave"],
        "title": scenario["title"],
        "critical": scenario["critical"],
        "result": "todo",
        "evidence": [],
        "note": "",
        "updated_at": None,
    }
    for field in (
        "order_id", "payment_id", "refund_id", "order_status", "payment_status",
        "refund_status", "expected_amount", "provider_amount", "currency",
        "provider_currency", "stock_before", "stock_after", "expected_stock_delta",
        "webhook_deliveries", "domain_effects",
    ):
        record[field] = None
    return record


def verified_admission_binding(root: Path = ROOT) -> dict[str, Any]:
    from pilot_admission import verify_default_admission
    from pilot_evidence import load_json

    errors = verify_default_admission(root)
    if errors:
        raise ValueError("Pilot admission is invalid: " + "; ".join(errors))
    manifest_path = root / DEFAULT_MANIFEST_PATH
    return build_admission_binding(manifest_path, load_json(manifest_path))


def new_state(admission_binding: Mapping[str, Any]) -> dict[str, Any]:
    now = utc_timestamp()
    state = {
        "schema_version": SCHEMA_VERSION,
        "pilot_name": "FLASHIN first 20 orders",
        "created_at": now,
        "updated_at": now,
        "decision": "NO-GO",
        "stop_reasons": [],
        "admission": json.loads(json.dumps(dict(admission_binding))),
        "scenarios": [_empty_record(scenario) for scenario in SCENARIOS],
    }
    _apply_report(state, validate_state(state, final=False))
    return state


def _scenario_records(state: dict[str, Any]) -> list[dict[str, Any]]:
    records = state.get("scenarios")
    if not isinstance(records, list):
        raise ValueError("Pilot state must contain a scenarios list")
    return records


def load_state(
    path: Path,
    *,
    expected_admission: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Pilot state not found: {path}. Run init first.") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Pilot state is not valid JSON: {exc}") from exc
    schema = state.get("schema_version")
    if schema == 1:
        raise ValueError(
            "Legacy pilot state schema 1 cannot be reused. Archive it and initialize "
            "a fresh admission-bound pilot state."
        )
    if schema != SCHEMA_VERSION:
        raise ValueError(f"Unsupported pilot state schema {schema}; expected {SCHEMA_VERSION}")
    if [item.get("number") for item in _scenario_records(state)] != [item["number"] for item in SCENARIOS]:
        raise ValueError("Pilot state scenario order does not match the current 20-order contract")
    if expected_admission is not None:
        require_admission_binding(state, expected_admission)
    return state


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _apply_report(state: dict[str, Any], report: dict[str, Any]) -> None:
    state["decision"] = report["decision"]
    state["stop_reasons"] = report["stop_reasons"]
    state["summary"] = report["summary"]


def save_state(path: Path, state: dict[str, Any], report: dict[str, Any]) -> None:
    state["updated_at"] = utc_timestamp()
    _apply_report(state, report)
    _atomic_write_text(path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    _atomic_write_text(path.with_name("live_pilot_summary.md"), render_markdown(state, report))


def _decimal(value: Any, field: str, errors: list[str], number: int) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        errors.append(f"#{number}: {field} must be a decimal number")
        return None


def _nonempty_ids(records: Iterable[dict[str, Any]], field: str) -> list[str]:
    return [str(record[field]).strip() for record in records if record.get(field)]


def _duplicates(values: Iterable[str]) -> list[str]:
    return sorted(value for value, count in Counter(values).items() if count > 1)


def _require(record: dict[str, Any], field: str, errors: list[str], label: str | None = None) -> None:
    if record.get(field) in (None, "", []):
        errors.append(f"#{record['number']}: missing {label or field}")


def validate_state(state: dict[str, Any], *, final: bool) -> dict[str, Any]:
    records = _scenario_records(state)
    errors: list[str] = []
    stop_reasons: list[str] = []

    for record in records:
        number = int(record.get("number", 0))
        scenario = SCENARIO_BY_NUMBER.get(number)
        if not scenario:
            errors.append(f"Unknown scenario number: {number}")
            continue
        result = record.get("result")
        if result not in ALLOWED_RESULTS:
            errors.append(f"#{number}: invalid result {result!r}")
            continue
        if result == "fail" and scenario["critical"]:
            stop_reasons.append(f"#{number} critical scenario failed: {scenario['title']}")
        if result != "pass":
            continue

        _require(record, "evidence", errors)
        requirements = {
            "requires_order": ("order_id", "order_status"),
            "requires_payment": ("payment_id", "payment_status", "expected_amount", "provider_amount", "currency", "provider_currency"),
            "requires_refund": ("refund_id", "refund_status"),
            "requires_stock": ("stock_before", "stock_after", "expected_stock_delta"),
        }
        for flag, fields in requirements.items():
            if scenario.get(flag):
                for field in fields:
                    _require(record, field, errors)

        if scenario["requires_order"] and record.get("order_status") != scenario["expected_order_status"]:
            errors.append(f"#{number}: order_status={record.get('order_status')!r}, expected {scenario['expected_order_status']!r}")

        expected_amount = _decimal(record.get("expected_amount"), "expected_amount", errors, number)
        provider_amount = _decimal(record.get("provider_amount"), "provider_amount", errors, number)
        if expected_amount is not None and provider_amount is not None and abs(expected_amount - provider_amount) > MONEY_TOLERANCE:
            stop_reasons.append(f"#{number}: payment amount mismatch {expected_amount} != {provider_amount}")

        currency = str(record.get("currency") or "").upper()
        provider_currency = str(record.get("provider_currency") or "").upper()
        if currency and provider_currency and currency != provider_currency:
            stop_reasons.append(f"#{number}: payment currency mismatch {currency} != {provider_currency}")

        stock_before, stock_after, expected_delta = (
            record.get("stock_before"), record.get("stock_after"), record.get("expected_stock_delta")
        )
        if isinstance(stock_after, (int, float)) and stock_after < 0:
            stop_reasons.append(f"#{number}: stock_after is negative ({stock_after})")
        if all(isinstance(value, (int, float)) for value in (stock_before, stock_after, expected_delta)):
            actual_delta = stock_before - stock_after
            if actual_delta != expected_delta:
                stop_reasons.append(f"#{number}: stock delta mismatch {actual_delta} != {expected_delta}")
        scenario_delta = scenario.get("expected_stock_delta")
        if scenario_delta is not None and expected_delta is not None and expected_delta != scenario_delta:
            stop_reasons.append(f"#{number}: scenario requires stock delta {scenario_delta}, recorded {expected_delta}")

        if scenario["requires_webhook_idempotency"]:
            if record.get("webhook_deliveries") is None or record["webhook_deliveries"] < 2:
                errors.append(f"#{number}: webhook_deliveries must be at least 2")
            if record.get("domain_effects") != 1:
                stop_reasons.append(f"#{number}: repeated webhook produced {record.get('domain_effects')!r} domain effects")

    completed = [record for record in records if record.get("result") == "pass"]
    for field, label in (("order_id", "order"), ("payment_id", "payment"), ("refund_id", "refund")):
        for duplicate in _duplicates(_nonempty_ids(completed, field)):
            stop_reasons.append(f"Duplicate {label} identifier across pilot scenarios: {duplicate}")

    counts = Counter(record.get("result", "unknown") for record in records)
    incomplete = len(records) - counts.get("pass", 0)
    if final and incomplete:
        errors.append(f"Final validation requires 20 passed scenarios; {incomplete} are not passed")
    if final and counts.get("blocked", 0):
        errors.append("Final validation cannot contain blocked scenarios")

    errors = list(dict.fromkeys(errors))
    stop_reasons = list(dict.fromkeys(stop_reasons))
    if stop_reasons:
        decision = "STOP"
    elif final and not errors and counts.get("pass", 0) == len(SCENARIOS):
        decision = "GO"
    else:
        decision = "NO-GO"
    return {
        "decision": decision,
        "stop_reasons": stop_reasons,
        "errors": errors,
        "warnings": [],
        "summary": {
            "total": len(records),
            "passed": counts.get("pass", 0),
            "failed": counts.get("fail", 0),
            "blocked": counts.get("blocked", 0),
            "running": counts.get("running", 0),
            "todo": counts.get("todo", 0),
            "critical_failed": sum(1 for record in records if record.get("critical") and record.get("result") == "fail"),
        },
    }


def record_scenario(state: dict[str, Any], number: int, **changes: Any) -> dict[str, Any]:
    if number not in SCENARIO_BY_NUMBER:
        raise ValueError(f"Scenario number must be between 1 and {len(SCENARIOS)}")
    if changes.get("result") is not None and changes["result"] not in ALLOWED_RESULTS - {"todo"}:
        raise ValueError("result must be running, pass, fail or blocked")
    record = _scenario_records(state)[number - 1]
    for field, value in changes.items():
        if value is not None:
            record[field] = value
    record["updated_at"] = utc_timestamp()
    return record


def render_markdown(state: dict[str, Any], report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# FLASHIN live pilot control", "", f"**Decision:** {report['decision']}", "",
        f"Passed: {summary['passed']}/20 · Failed: {summary['failed']} · Blocked: {summary['blocked']} · Running: {summary['running']} · Todo: {summary['todo']}", "",
    ]
    for title, items in (("STOP reasons", report["stop_reasons"]), ("Validation errors", report["errors"])):
        if items:
            lines.extend([f"## {title}", "", *(f"- {item}" for item in items), ""])
    lines.extend([
        "## Scenarios", "",
        "| # | Wave | Result | Critical | Scenario | Order | Payment | Evidence |",
        "|---:|---:|---|:---:|---|---|---|---:|",
    ])
    for record in _scenario_records(state):
        lines.append(
            f"| {record['number']} | {record['wave']} | {record['result']} | "
            f"{'yes' if record['critical'] else 'no'} | {str(record['title']).replace('|', chr(92) + '|')} | "
            f"{record.get('order_id') or '—'} | {record.get('payment_id') or '—'} | {len(record.get('evidence') or [])} |"
        )
    lines.extend([
        "", "## Decision rule", "",
        "- **STOP**: критический сценарий провален либо нарушен денежный, валютный, складской или idempotency-инвариант.",
        "- **NO-GO**: пилот не завершён или доказательства неполны.",
        "- **GO**: финальная проверка пройдена, все 20 сценариев имеют result=pass и нет ошибок.", "",
    ])
    return "\n".join(lines)


def _state_path(args: argparse.Namespace) -> Path:
    return Path(args.state)


def _finish(path: Path, state: dict[str, Any], *, final: bool = False) -> int:
    report = validate_state(state, final=final)
    save_state(path, state, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["decision"] == "STOP":
        return 2
    if final and report["decision"] != "GO":
        return 1
    return 0


def command_init(args: argparse.Namespace) -> int:
    path = _state_path(args)
    if path.exists() and not args.force:
        raise ValueError(f"Pilot state already exists: {path}. Use --force only for an intentional reset.")
    return _finish(path, new_state(args.admission_binding))


def command_record(args: argparse.Namespace) -> int:
    path = _state_path(args)
    state = load_state(path, expected_admission=args.admission_binding)
    if args.number not in SCENARIO_BY_NUMBER:
        raise ValueError(f"Scenario number must be between 1 and {len(SCENARIOS)}")
    current = _scenario_records(state)[args.number - 1]
    evidence = [] if args.replace_evidence else list(current.get("evidence") or [])
    evidence.extend(args.evidence or [])
    record_scenario(
        state, args.number, result=args.result, order_id=args.order_id, payment_id=args.payment_id,
        refund_id=args.refund_id, order_status=args.order_status, payment_status=args.payment_status,
        refund_status=args.refund_status, expected_amount=args.expected_amount,
        provider_amount=args.provider_amount, currency=args.currency.upper() if args.currency else None,
        provider_currency=args.provider_currency.upper() if args.provider_currency else None,
        stock_before=args.stock_before, stock_after=args.stock_after,
        expected_stock_delta=args.expected_stock_delta, webhook_deliveries=args.webhook_deliveries,
        domain_effects=args.domain_effects, evidence=evidence, note=args.note,
    )
    return _finish(path, state)


def command_status(args: argparse.Namespace) -> int:
    path = _state_path(args)
    return _finish(path, load_state(path, expected_admission=args.admission_binding))


def command_validate(args: argparse.Namespace) -> int:
    path = _state_path(args)
    return _finish(path, load_state(path, expected_admission=args.admission_binding), final=args.final)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", default=str(DEFAULT_STATE_PATH), help="Pilot state JSON path")
    subparsers = parser.add_subparsers(dest="command")
    init_parser = subparsers.add_parser("init", help="Create a fresh 20-order pilot state")
    init_parser.add_argument("--force", action="store_true", help="Replace an existing state")
    init_parser.set_defaults(handler=command_init)

    record_parser = subparsers.add_parser("record", help="Record one pilot scenario result")
    record_parser.add_argument("--number", type=int, required=True)
    record_parser.add_argument("--result", choices=sorted(ALLOWED_RESULTS - {"todo"}), required=True)
    for option in ("order-id", "payment-id", "refund-id", "order-status", "payment-status", "refund-status", "expected-amount", "provider-amount", "currency", "provider-currency", "note"):
        record_parser.add_argument(f"--{option}")
    for option in ("stock-before", "stock-after", "expected-stock-delta", "webhook-deliveries", "domain-effects"):
        record_parser.add_argument(f"--{option}", type=int)
    record_parser.add_argument("--evidence", action="append", default=[])
    record_parser.add_argument("--replace-evidence", action="store_true")
    record_parser.set_defaults(handler=command_record)

    status_parser = subparsers.add_parser("status", help="Recalculate and print current decision")
    status_parser.set_defaults(handler=command_status)
    validate_parser = subparsers.add_parser("validate", help="Validate pilot state")
    validate_parser.add_argument("--final", action="store_true", help="Require all 20 scenarios to pass")
    validate_parser.set_defaults(handler=command_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        path = Path(args.state)
        return main(["--state", str(path), "status" if path.exists() else "init"])
    try:
        args.admission_binding = verified_admission_binding(ROOT)
        return args.handler(args)
    except ValueError as exc:
        parser.error(str(exc))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
