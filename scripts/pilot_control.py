#!/usr/bin/env python3
"""Fail-closed control plane for the first 20 FLASHIN pilot orders."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping

from pilot_control_audit import (
    APPROVAL_ROLES,
    approved_operators,
    build_audit_entry,
    normalize_mutation,
    require_audit_log,
    validate_record_mutation,
)
from pilot_control_binding import build_admission_binding, require_admission_binding
from pilot_control_chain import (
    require_state_chain,
    signed_state_sha256,
)
from pilot_control_io import durable_atomic_write_text
from pilot_control_lock import (
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    exclusive_state_lock,
)
from pilot_evidence import require_signing_secret, sign_payload, verify_payload_signature
from pilot_readiness import read_env
from script_time import utc_timestamp

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 6
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
    (7, 2, "Отмена неоплаченного заказа", True, "cancelled", "os", "резерв освобождён, списания нет", 0),
    (8, 3, "Заказ из нескольких позиций", True, "paid", "ops", "сумма строк и совокупное списание", None),
    (9, 3, "Пограничный остаток", True, "paid", "ops", "остаток не уходит ниже нуля", None),
    (10, 3, "Поздний платёж после истечения резерва", True, "payment_review_required", "ops", "нет скрытого списания, создан review case", 0),
    (11, 3, "Support ticket после заказа", False, "paid", "ops", "тикет связан с order ID", None),
    (12, 3, "Fulfillment picking", True, "paid", "ops", "создана одна задача комплектации", None),
    (13, 4, "Полный возврат", True, "refunded", "opsr", "деньги, товар и бонусы возвращены один раз", None),
    (14, 4, "Частичный возврат", True, "partially_refunded", "opsr", "возвращена только подтверждённая часть", None),
    (15, 4, "Повторный refund callback", True, "refunded", "opsri", "два callback, один возврат и одно оприходование", None),
    (16, 4, "Аномалия платежа на ручном review", True, "payment_review_required", "ops", "оператор видит причину и не проводит заказ автоматически", 0),
    (17, 4, "MoySklad конфликт остатка", True, "payment_review_required", "o", "конфликт видим и имеет владельца", None),
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


def verified_admission_context(
    root: Path = ROOT,
) -> tuple[dict[str, Any], dict[str, str]]:
    from pilot_admission import verify_default_admission
    from pilot_evidence import load_json

    errors = verify_default_admission(root)
    if errors:
        raise ValueError("Pilot admission is invalid: " + "; ".join(errors))
    manifest_path = root / DEFAULT_MANIFEST_PATH
    manifest = load_json(manifest_path)
    return build_admission_binding(manifest_path, manifest), approved_operators(manifest)


def verified_admission_binding(root: Path = ROOT) -> dict[str, Any]:
    return verified_admission_context(root)[0]


def pilot_signing_secret(root: Path = ROOT) -> str:
    return require_signing_secret(read_env(root / ".env"))


def new_state(
    admission_binding: Mapping[str, Any],
    *,
    initial_audit: Mapping[str, Any],
) -> dict[str, Any]:
    now = str(initial_audit.get("changed_at", "")).strip() or utc_timestamp()
    state = {
        "schema_version": SCHEMA_VERSION,
        "database_evidence_contract": 1,
        "pilot_name": "FLASHIN first 20 orders",
        "created_at": now,
        "updated_at": now,
        "decision": "NO-GO",
        "stop_reasons": [],
        "revision": 1,
        "state_history_sha256": [],
        "audit_log": [json.loads(json.dumps(dict(initial_audit)))],
        "admission": json.loads(json.dumps(dict(admission_binding))),
        "scenarios": [_empty_record(scenario) for scenario in SCENARIOS],
    }
    require_audit_log(state)
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
    secret: str | None = None,
    approved_operator_names: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Pilot state not found: {path}. Run init first.") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Pilot state is not valid JSON: {exc}") from exc
    if not isinstance(state, dict):
        raise ValueError("Pilot state must contain a JSON object")
    schema = state.get("schema_version")
    if schema == 1:
        raise ValueError(
            "Legacy pilot state schema 1 cannot be reused. Archive it and initialize "
            "a fresh signed admission-bound pilot state."
        )
    if schema == 2:
        raise ValueError(
            "Unsigned pilot state schema 2 cannot be reused. Archive it and initialize "
            "a fresh replay-resistant pilot state."
        )
    if schema == 3:
        raise ValueError(
            "Replay-vulnerable pilot state schema 3 cannot be reused. Archive it and "
            "initialize a fresh replay-resistant pilot state."
        )
    if schema == 4:
        raise ValueError(
            "Unattributed pilot state schema 4 cannot be reused. Archive it and "
            "initialize a fresh accountable pilot state."
        )
    if schema == 5:
        raise ValueError(
            "Database-unverified pilot state schema 5 cannot be reused. Archive it and "
            "initialize a fresh database-bound pilot state."
        )
    if schema != SCHEMA_VERSION:
        raise ValueError(f"Unsupported pilot state schema {schema}; expected {SCHEMA_VERSION}")
    if state.get("database_evidence_contract") != 1:
        raise ValueError("Pilot database evidence contract is missing or unsupported")
    if not secret:
        raise ValueError("Pilot control signing secret is required")
    if not verify_payload_signature(state, secret):
        raise ValueError("Pilot control state signature is invalid")
    require_state_chain(state)
    if approved_operator_names is not None:
        require_audit_log(state, approvals=approved_operator_names)
    if [item.get("number") for item in _scenario_records(state)] != [item["number"] for item in SCENARIOS]:
        raise ValueError("Pilot state scenario order does not match the current 20-order contract")
    if expected_admission is not None:
        require_admission_binding(state, expected_admission)
    return state


def _apply_report(state: dict[str, Any], report: dict[str, Any]) -> None:
    state["decision"] = report["decision"]
    state["stop_reasons"] = report["stop_reasons"]
    state["summary"] = report["summary"]


def save_state(
    path: Path,
    state: dict[str, Any],
    report: dict[str, Any],
    *,
    secret: str,
    allow_replace: bool = False,
    lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    approved_operator_names: Mapping[str, str],
    mutation: Mapping[str, Any] | None = None,
) -> None:
    with exclusive_state_lock(path, timeout_seconds=lock_timeout_seconds):
        if "signature" in state:
            try:
                parent_state = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                raise ValueError("Pilot control parent state is missing before update") from exc
            except json.JSONDecodeError as exc:
                raise ValueError("Pilot control parent state is invalid JSON") from exc
            if not isinstance(parent_state, dict):
                raise ValueError("Pilot control parent state must contain a JSON object")
            if not verify_payload_signature(parent_state, secret):
                raise ValueError("Pilot control parent state signature is invalid")
            require_state_chain(parent_state)
            require_audit_log(parent_state, approvals=approved_operator_names)
            if mutation is None:
                raise ValueError("Pilot record mutation audit metadata is required")
            if (
                state.get("signature") != parent_state.get("signature")
                or state.get("revision") != parent_state.get("revision")
                or state.get("state_history_sha256")
                != parent_state.get("state_history_sha256")
                or state.get("audit_log") != parent_state.get("audit_log")
            ):
                raise ValueError("Pilot control state changed concurrently before update")
            mutation_errors = validate_record_mutation(parent_state, state, mutation)
            if mutation_errors:
                raise ValueError("; ".join(mutation_errors))
            previous_hash = signed_state_sha256(parent_state)
            state["state_history_sha256"] = [
                *list(parent_state["state_history_sha256"]),
                previous_hash,
            ]
            state["revision"] = int(parent_state["revision"]) + 1
            changed_at = utc_timestamp()
            state["audit_log"] = [
                *list(parent_state["audit_log"]),
                build_audit_entry(
                    mutation,
                    revision=int(state["revision"]),
                    parent_state_sha256=previous_hash,
                    changed_at=changed_at,
                ),
            ]
        else:
            require_state_chain(state)
            require_audit_log(state, approvals=approved_operator_names)
            if mutation is not None:
                raise ValueError("Pilot init mutation must be embedded in initial audit")
            if state.get("revision") != 1 or state.get("state_history_sha256") != []:
                raise ValueError("Initial pilot control state lineage is invalid")
            if path.exists() and not allow_replace:
                raise ValueError("Pilot control state appeared concurrently before initialization")
        if mutation is not None:
            state["updated_at"] = changed_at
        else:
            state["updated_at"] = str(state["audit_log"][0]["changed_at"])
        require_audit_log(state, approvals=approved_operator_names)
        _apply_report(state, report)
        signed_state = sign_payload(state, secret)
        state.clear()
        state.update(signed_state)
        durable_atomic_write_text(
            path, json.dumps(state, ensure_ascii=False, indent=2) + "\n"
        )
        durable_atomic_write_text(
            path.with_name("live_pilot_summary.md"),
            render_markdown(state, report),
        )


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
        _require(record, "order_id", errors)
        _require(record, "order_status", errors)
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

        if record.get("order_status") != scenario["expected_order_status"]:
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
    state_sha = signed_state_sha256(state)
    last_audit = state["audit_log"][-1]
    lines = [
        "# FLASHIN live pilot control",
        "",
        f"**Decision:** {report['decision']}",
        "",
        f"State revision: `{state.get('revision')}`",
        f"State SHA-256: `{state_sha}`",
        f"Last accountable mutation: `{last_audit['operator_role']}` / {last_audit['operator_name']}",
        f"Mutation reason: {last_audit['reason']}",
        "Source: signed JSON state. This Markdown file is derived and non-authoritative.",
        "",
        f"Passed: {summary['passed']}/20 · Failed: {summary['failed']} · Blocked: {summary['blocked']} · Running: {summary['running']} · Todo: {summary['todo']}",
        "",
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


def refresh_summary(
    path: Path,
    *,
    expected_admission: Mapping[str, Any],
    secret: str,
    approved_operator_names: Mapping[str, str],
    final: bool = False,
    lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> tuple[dict[str, Any], dict[str, Any]]:
    with exclusive_state_lock(path, timeout_seconds=lock_timeout_seconds):
        state = load_state(
            path,
            expected_admission=expected_admission,
            secret=secret,
            approved_operator_names=approved_operator_names,
        )
        report = validate_state(state, final=final)
        durable_atomic_write_text(
            path.with_name("live_pilot_summary.md"),
            render_markdown(state, report),
        )
        return state, report


def _state_path(args: argparse.Namespace) -> Path:
    return Path(args.state)


def _database_evidence_errors(
    state: Mapping[str, Any],
    *,
    final: bool,
) -> list[str]:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from backend.database import SessionLocal
        from backend.pilot_models import PilotRuntimeState
        from backend.services.pilot_database_evidence import (
            validate_pilot_database_evidence,
        )

        db = SessionLocal()
        try:
            runtime = (
                db.query(PilotRuntimeState)
                .filter(PilotRuntimeState.id == 1)
                .first()
            )
            return validate_pilot_database_evidence(
                db,
                state,
                runtime,
                final=final,
            )
        finally:
            db.close()
    except Exception as exc:
        return [f"pilot database evidence validation failed: {exc}"]


def _merge_database_errors(
    report: dict[str, Any],
    errors: Iterable[str],
) -> None:
    merged = list(
        dict.fromkeys(
            [*report.get("errors", []), *[str(item) for item in errors]]
        )
    )
    report["errors"] = merged
    if merged and report.get("decision") != "STOP":
        report["decision"] = "NO-GO"


def _refresh_with_database(
    path: Path,
    *,
    expected_admission: Mapping[str, Any],
    secret: str,
    approved_operator_names: Mapping[str, str],
    final: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    state, report = refresh_summary(
        path,
        expected_admission=expected_admission,
        secret=secret,
        approved_operator_names=approved_operator_names,
        final=final,
    )
    _merge_database_errors(
        report,
        _database_evidence_errors(state, final=final),
    )
    durable_atomic_write_text(
        path.with_name("live_pilot_summary.md"),
        render_markdown(state, report),
    )
    return state, report


def _finish(
    path: Path,
    state: dict[str, Any],
    *,
    secret: str,
    final: bool = False,
    persist: bool = True,
    allow_replace: bool = False,
    approved_operator_names: Mapping[str, str],
    mutation: Mapping[str, Any] | None = None,
) -> int:
    report = validate_state(state, final=final)
    if persist:
        save_state(
            path,
            state,
            report,
            secret=secret,
            allow_replace=allow_replace,
            approved_operator_names=approved_operator_names,
            mutation=mutation,
        )
    return _report_exit(report, final=final)


def _report_exit(report: Mapping[str, Any], *, final: bool) -> int:
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["decision"] == "STOP":
        return 2
    if final and report["decision"] != "GO":
        return 1
    return 0


def _mutation_from_args(
    args: argparse.Namespace,
    *,
    operation: str,
    scenario_number: int | None = None,
    result: str | None = None,
) -> dict[str, Any]:
    return normalize_mutation(
        operation=operation,
        operator_role=args.operator_role,
        operator_name=args.operator,
        reason=args.reason,
        approvals=args.approved_operators,
        scenario_number=scenario_number,
        result=result,
        force_reset=bool(getattr(args, "force", False)),
    )


def command_init(args: argparse.Namespace) -> int:
    path = _state_path(args)
    if path.exists() and not args.force:
        raise ValueError(f"Pilot state already exists: {path}. Use --force only for an intentional reset.")
    mutation = _mutation_from_args(args, operation="init")
    initial_audit = build_audit_entry(
        mutation,
        revision=1,
        parent_state_sha256=None,
    )
    return _finish(
        path,
        new_state(args.admission_binding, initial_audit=initial_audit),
        secret=args.signing_secret,
        allow_replace=args.force,
        approved_operator_names=args.approved_operators,
    )


def command_record(args: argparse.Namespace) -> int:
    path = _state_path(args)
    state = load_state(
        path, expected_admission=args.admission_binding, secret=args.signing_secret
    )
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
    database_errors = _database_evidence_errors(state, final=False)
    if database_errors:
        raise ValueError(
            "Pilot scenario database evidence is invalid: "
            + "; ".join(database_errors)
        )
    return _finish(
        path,
        state,
        secret=args.signing_secret,
        approved_operator_names=args.approved_operators,
        mutation=_mutation_from_args(
            args,
            operation="record",
            scenario_number=args.number,
            result=args.result,
        ),
    )


def command_status(args: argparse.Namespace) -> int:
    path = _state_path(args)
    _, report = _refresh_with_database(
        path,
        expected_admission=args.admission_binding,
        secret=args.signing_secret,
        approved_operator_names=args.approved_operators,
        final=False,
    )
    return _report_exit(report, final=False)


def command_validate(args: argparse.Namespace) -> int:
    path = _state_path(args)
    _, report = _refresh_with_database(
        path,
        expected_admission=args.admission_binding,
        secret=args.signing_secret,
        approved_operator_names=args.approved_operators,
        final=args.final,
    )
    return _report_exit(report, final=args.final)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", default=str(DEFAULT_STATE_PATH), help="Pilot state JSON path")
    subparsers = parser.add_subparsers(dest="command")
    def add_mutation_identity(target: argparse.ArgumentParser) -> None:
        target.add_argument("--operator-role", choices=APPROVAL_ROLES, required=True)
        target.add_argument("--operator", required=True)
        target.add_argument("--reason", required=True)

    init_parser = subparsers.add_parser("init", help="Create a fresh 20-order pilot state")
    init_parser.add_argument("--force", action="store_true", help="Replace an existing state")
    add_mutation_identity(init_parser)
    init_parser.set_defaults(handler=command_init)

    record_parser = subparsers.add_parser("record", help="Record one pilot scenario result")
    add_mutation_identity(record_parser)
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
        if path.exists():
            return main(["--state", str(path), "status"])
        parser.error(
            "Pilot initialization requires explicit --operator-role, --operator and --reason"
        )
    try:
        (
            args.admission_binding,
            args.approved_operators,
        ) = verified_admission_context(ROOT)
        args.signing_secret = pilot_signing_secret(ROOT)
        return args.handler(args)
    except ValueError as exc:
        parser.error(str(exc))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
