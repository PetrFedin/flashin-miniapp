from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    Path(path).write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"Expected one match in {path}: {old[:120]!r}; found {count}"
        )
    write(path, text.replace(old, new, 1))


# Pilot control state: schema v6, canonical statuses and operator-side DB checks.
replace_once("scripts/pilot_control.py", "import json\n", "import json\nimport sys\n")
replace_once(
    "scripts/pilot_control.py",
    "SCHEMA_VERSION = 5",
    "SCHEMA_VERSION = 6",
)
replace_once(
    "scripts/pilot_control.py",
    '(7, 2, "Отмена неоплаченного заказа", True, "canceled", "os",',
    '(7, 2, "Отмена неоплаченного заказа", True, "cancelled", "os",',
)
replace_once(
    "scripts/pilot_control.py",
    '(10, 3, "Поздний платёж после истечения резерва", True, "review", "ops",',
    '(10, 3, "Поздний платёж после истечения резерва", True, "payment_review_required", "ops",',
)
replace_once(
    "scripts/pilot_control.py",
    '(16, 4, "Аномалия платежа на ручном review", True, "review", "ops",',
    '(16, 4, "Аномалия платежа на ручном review", True, "payment_review_required", "ops",',
)
replace_once(
    "scripts/pilot_control.py",
    '(17, 4, "MoySklad конфликт остатка", True, "review", "",',
    '(17, 4, "MoySklad конфликт остатка", True, "payment_review_required", "o",',
)
replace_once(
    "scripts/pilot_control.py",
    '        "schema_version": SCHEMA_VERSION,\n        "pilot_name":',
    '        "schema_version": SCHEMA_VERSION,\n'
    '        "database_evidence_contract": 1,\n'
    '        "pilot_name":',
)
replace_once(
    "scripts/pilot_control.py",
    '''    if schema == 4:
        raise ValueError(
            "Unattributed pilot state schema 4 cannot be reused. Archive it and "
            "initialize a fresh accountable pilot state."
        )
    if schema != SCHEMA_VERSION:
''',
    '''    if schema == 4:
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
''',
)
replace_once(
    "scripts/pilot_control.py",
    '    if not secret:\n        raise ValueError("Pilot control signing secret is required")',
    '    if state.get("database_evidence_contract") != 1:\n'
    '        raise ValueError("Pilot database evidence contract is missing or unsupported")\n'
    '    if not secret:\n'
    '        raise ValueError("Pilot control signing secret is required")',
)
replace_once(
    "scripts/pilot_control.py",
    '        _require(record, "evidence", errors)\n        requirements = {',
    '        _require(record, "evidence", errors)\n'
    '        _require(record, "order_id", errors)\n'
    '        _require(record, "order_status", errors)\n'
    '        requirements = {',
)
replace_once(
    "scripts/pilot_control.py",
    '        if scenario["requires_order"] and record.get("order_status") != scenario["expected_order_status"]:',
    '        if record.get("order_status") != scenario["expected_order_status"]:',
)
helper = '''def _database_evidence_errors(
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


'''
replace_once(
    "scripts/pilot_control.py",
    "def _state_path(args: argparse.Namespace) -> Path:\n"
    "    return Path(args.state)\n\n\n",
    "def _state_path(args: argparse.Namespace) -> Path:\n"
    "    return Path(args.state)\n\n\n"
    + helper,
)
replace_once(
    "scripts/pilot_control.py",
    '''    return _finish(
        path,
        state,
        secret=args.signing_secret,
        approved_operator_names=args.approved_operators,
        mutation=_mutation_from_args(
''',
    '''    database_errors = _database_evidence_errors(state, final=False)
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
''',
)
replace_once(
    "scripts/pilot_control.py",
    '''def command_status(args: argparse.Namespace) -> int:
    path = _state_path(args)
    _, report = refresh_summary(
        path,
        expected_admission=args.admission_binding,
        secret=args.signing_secret,
        approved_operator_names=args.approved_operators,
    )
    return _report_exit(report, final=False)


def command_validate(args: argparse.Namespace) -> int:
    path = _state_path(args)
    _, report = refresh_summary(
        path,
        expected_admission=args.admission_binding,
        secret=args.signing_secret,
        approved_operator_names=args.approved_operators,
        final=args.final,
    )
    return _report_exit(report, final=args.final)
''',
    '''def command_status(args: argparse.Namespace) -> int:
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
''',
)

# Backend runtime: validate the exact signed state against DB before checkout.
replace_once(
    "backend/services/pilot_runtime.py",
    "from ..database import utcnow_naive\n",
    "from ..database import utcnow_naive\n"
    "from .pilot_database_evidence import validate_pilot_database_evidence\n",
)
replace_once(
    "backend/services/pilot_runtime.py",
    "    validated_anchor: dict[str, Any] | None = None,\n) -> list[str]:",
    "    validated_anchor: dict[str, Any] | None = None,\n"
    "    validated_pilot_state: dict[str, Any] | None = None,\n"
    ") -> list[str]:",
)
replace_once(
    "backend/services/pilot_runtime.py",
    '    if pilot_state.get("schema_version") != 5:',
    '    if pilot_state.get("schema_version") != 6:',
)
replace_once(
    "backend/services/pilot_runtime.py",
    '    elif not verify_payload_signature(pilot_state, secret):',
    '    elif pilot_state.get("database_evidence_contract") != 1:\n'
    '        errors.append("pilot database evidence contract is missing or unsupported")\n'
    '    elif not verify_payload_signature(pilot_state, secret):',
)
replace_once(
    "backend/services/pilot_runtime.py",
    '''    if not unique_errors and validated_anchor is not None:
        validated_anchor.update(state_anchor(pilot_state))
    return unique_errors
''',
    '''    if not unique_errors and validated_anchor is not None:
        validated_anchor.update(state_anchor(pilot_state))
    if not unique_errors and validated_pilot_state is not None:
        validated_pilot_state.clear()
        validated_pilot_state.update(json.loads(json.dumps(pilot_state)))
    return unique_errors
''',
)
replace_once(
    "backend/services/pilot_runtime.py",
    '''    current_anchor: dict[str, Any] = {}
    file_errors = validate_runtime_files(
        state, settings, env=env, validated_anchor=current_anchor
    )
''',
    '''    current_anchor: dict[str, Any] = {}
    current_pilot_state: dict[str, Any] = {}
    file_errors = validate_runtime_files(
        state,
        settings,
        env=env,
        validated_anchor=current_anchor,
        validated_pilot_state=current_pilot_state,
    )
''',
)
replace_once(
    "backend/services/pilot_runtime.py",
    '''        raise _integrity_failure()

    state.pilot_state_revision = int(current_anchor["revision"])
''',
    '''        raise _integrity_failure()
    database_errors = validate_pilot_database_evidence(
        db,
        current_pilot_state,
        state,
        final=False,
    )
    if database_errors:
        raise _integrity_failure()

    state.pilot_state_revision = int(current_anchor["revision"])
''',
)

# Host/runtime arm validates the same DB contract.
replace_once(
    "scripts/pilot_runtime.py",
    '        if pilot_state.get("schema_version") != 5:',
    '        if pilot_state.get("schema_version") != 6:',
)
replace_once(
    "scripts/pilot_runtime.py",
    '        if not verify_payload_signature(pilot_state, secret):',
    '        if pilot_state.get("database_evidence_contract") != 1:\n'
    '            raise ValueError("Pilot database evidence contract is missing or unsupported")\n'
    '        if not verify_payload_signature(pilot_state, secret):',
)
replace_once(
    "scripts/pilot_runtime.py",
    "    settings = get_settings()\n",
    "    from backend.services.pilot_database_evidence import (\n"
    "        validate_pilot_database_evidence,\n"
    "    )\n\n"
    "    settings = get_settings()\n",
)
replace_once(
    "scripts/pilot_runtime.py",
    '''            verified_anchor: dict[str, Any] = {}
            errors = validate_runtime_files(
                state, settings, validated_anchor=verified_anchor
            )
            if errors:
                raise ValueError("Active pilot runtime evidence is invalid: " + "; ".join(errors))
''',
    '''            verified_anchor: dict[str, Any] = {}
            verified_pilot_state: dict[str, Any] = {}
            errors = validate_runtime_files(
                state,
                settings,
                validated_anchor=verified_anchor,
                validated_pilot_state=verified_pilot_state,
            )
            if errors:
                raise ValueError("Active pilot runtime evidence is invalid: " + "; ".join(errors))
            database_errors = validate_pilot_database_evidence(
                db,
                verified_pilot_state,
                state,
                final=False,
            )
            if database_errors:
                raise ValueError(
                    "Active pilot database evidence is invalid: "
                    + "; ".join(database_errors)
                )
''',
)
replace_once(
    "scripts/pilot_runtime.py",
    '''        verified_anchor: dict[str, Any] = {}
        errors = validate_runtime_files(
            state, settings, validated_anchor=verified_anchor
        )
        if errors:
            raise ValueError("Pilot runtime evidence is invalid: " + "; ".join(errors))
''',
    '''        verified_anchor: dict[str, Any] = {}
        verified_pilot_state: dict[str, Any] = {}
        errors = validate_runtime_files(
            state,
            settings,
            validated_anchor=verified_anchor,
            validated_pilot_state=verified_pilot_state,
        )
        if errors:
            raise ValueError("Pilot runtime evidence is invalid: " + "; ".join(errors))
        database_errors = validate_pilot_database_evidence(
            db,
            verified_pilot_state,
            state,
            final=False,
        )
        if database_errors:
            raise ValueError(
                "Pilot database evidence is invalid: "
                + "; ".join(database_errors)
            )
''',
)

# Immutable capability v16.
replace_once(
    "scripts/pilot_release_contract.py",
    "CAPABILITY_VERSION = 15",
    "CAPABILITY_VERSION = 16",
)
capability = read("scripts/pilot_release_capability.py")
capability = capability.replace(
    '    "backend/services/pilot_runtime.py",\n',
    '    "backend/services/pilot_runtime.py",\n'
    '    "backend/services/pilot_database_evidence.py",\n',
    1,
)
capability = capability.replace(
    '    "backend/tests/test_pilot_runtime.py",\n',
    '    "backend/tests/test_pilot_runtime.py",\n'
    '    "backend/tests/test_pilot_database_evidence.py",\n',
    1,
)
capability = capability.replace(
    '("CAPABILITY_VERSION = 15",)',
    '("CAPABILITY_VERSION = 16",)',
    1,
)
capability = capability.replace(
    '("SCHEMA_VERSION = 5", "database_evidence_contract",',
    '("SCHEMA_VERSION = 6", "database_evidence_contract",',
    1,
)
if '("SCHEMA_VERSION = 6", "database_evidence_contract",' not in capability:
    capability = capability.replace(
        '("SCHEMA_VERSION = 5",',
        '("SCHEMA_VERSION = 6", "database_evidence_contract",',
        1,
    )
anchor = (
    '            _require_markers(bundle, files, "backend/services/pilot_runtime.py", '
)
index = capability.find(anchor)
if index < 0:
    raise SystemExit("pilot runtime capability marker anchor is missing")
line_end = capability.find("\n", index)
capability = (
    capability[: line_end + 1]
    + '            _require_markers(bundle, files, "backend/services/pilot_database_evidence.py", '
      '("def validate_pilot_database_evidence(", "pilot slot order_id", '
      '"PostgreSQL payment", "PostgreSQL refund", "final GO scenario order IDs"), errors)\n'
    + '            _require_markers(bundle, files, "backend/tests/test_pilot_database_evidence.py", '
      '("test_exact_completed_twenty_order_database_evidence_is_accepted", '
      '"test_missing_or_wrong_slot_order_fails_closed", '
      '"test_payment_refund_status_and_amount_are_read_from_postgresql", '
      '"test_final_go_rejects_active_or_incomplete_runtime"), errors)\n'
    + capability[line_end + 1 :]
)
capability = capability.replace(
    '"approved_operators(manifest)", "state.pilot_state_revision"',
    '"approved_operators(manifest)", "validate_pilot_database_evidence(", '
    '"state.pilot_state_revision"',
    1,
)
capability = capability.replace(
    '"approved_operators(manifest)", "pilot_state_revision"',
    '"approved_operators(manifest)", "validate_pilot_database_evidence(", '
    '"pilot_state_revision"',
    1,
)
write("scripts/pilot_release_capability.py", capability)

# Test fixtures and static contract assertions.
replace_once(
    "backend/tests/test_pilot_runtime.py",
    '            "version": 15,',
    '            "version": 16,',
)
replace_once(
    "backend/tests/test_pilot_runtime.py",
    '        "schema_version": 5,\n        "revision": 1,',
    '        "schema_version": 6,\n'
    '        "database_evidence_contract": 1,\n'
    '        "revision": 1,',
)
replace_once(
    "backend/tests/test_pilot_runtime_cli.py",
    "def test_host_arm_requires_accountable_schema_v5_control_state():",
    "def test_host_arm_requires_database_bound_schema_v6_control_state():",
)
replace_once(
    "backend/tests/test_pilot_runtime_cli.py",
    '    assert \'pilot_state.get("schema_version") != 5\' in source',
    '    assert \'pilot_state.get("schema_version") != 6\' in source\n'
    '    assert "database_evidence_contract" in source\n'
    '    assert "validate_pilot_database_evidence(" in source',
)
replace_once(
    "backend/tests/test_pilot_release_capability_repository.py",
    "test_current_repository_archive_satisfies_capability_v15",
    "test_current_repository_archive_satisfies_capability_v16",
)
replace_once(
    "backend/tests/test_pilot_release_capability_repository.py",
    'release_id="current-capability-v15"',
    'release_id="current-capability-v16"',
)

# Pure state validation now treats every passed live scenario as one accepted order.
control_test = read("backend/tests/test_pilot_control.py")
control_test = control_test.replace(
    '    if scenario.get("requires_order"):\n'
    '        changes.update(\n'
    '            order_id=f"order-{number}",\n'
    '            order_status=scenario["expected_order_status"],\n'
    '        )\n',
    '    changes.update(\n'
    '        order_id=str(number),\n'
    '        order_status=scenario["expected_order_status"],\n'
    '    )\n',
    1,
)
write("backend/tests/test_pilot_control.py", control_test)

# Documentation and E2E coverage matrix.
runbook_path = "docs/pilot/admission_bound_state_migration.md"
runbook = read(runbook_path)
runbook = runbook.replace("state schema v5", "state schema v6")
runbook = runbook.replace(
    "same signed pilot capability v15",
    "same signed pilot capability v16",
)
runbook = runbook.replace(
    "accountable schema v5 state",
    "database-bound schema v6 state",
)
runbook = runbook.replace(
    "Existing schema v1, v2, v3 or v4 state",
    "Existing schema v1, v2, v3, v4 or v5 state",
)
runbook = runbook.replace(
    "Schema v4 has replay protection but no admission-owner audit. All four",
    "Schema v4 has replay protection but no admission-owner audit. Schema v5 "
    "has accountable signed mutations but does not prove recorded IDs against "
    "PostgreSQL. All five",
)
runbook = runbook.replace(
    "unattributed schema v4 state",
    "unattributed schema v4 or database-unverified schema v5 state",
)
runbook += '''

## Database-bound scenario evidence

A scenario may be recorded as `pass` only after its exact sequence has a
PostgreSQL `pilot_order_slots` row. The signed `order_id` must equal that
slot's order. Provider payment and refund identifiers must resolve to exactly
one row owned by the same order, while recorded order, payment and return
statuses, amounts and currencies must match PostgreSQL. Final GO additionally
requires runtime status `completed`, exactly 20 accepted orders and an exact
sequence-by-sequence match between all 20 scenarios and slots. Database
unavailability or any mismatch fails closed.
'''
write(runbook_path, runbook)

matrix_path = "docs/pilot/end_to_end_coverage_matrix.md"
matrix = read(matrix_path)
row = (
    "| Database-bound first-20 evidence | Signed scenario claims → exact "
    "PostgreSQL pilot slot/order/payment/refund/status/amount/currency → final "
    "completed 20-slot equality | PASS | Capability v16 fails closed on "
    "fabricated, unrelated, missing or drifted database evidence. |"
)
if row not in matrix:
    matrix += "\n\n" + row + "\n"
write(matrix_path, matrix)
