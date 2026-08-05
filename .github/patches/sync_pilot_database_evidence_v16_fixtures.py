from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    content = path.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise SystemExit(
            f"Expected one fixture marker in {path}: {old[:100]!r}; found {count}"
        )
    path.write_text(content.replace(old, new, 1), encoding="utf-8")


signature = Path("backend/tests/test_pilot_control_signature.py")
replace_once(
    signature,
    'assert state["schema_version"] == 5',
    'assert state["schema_version"] == 6',
)

capability = Path("backend/tests/test_pilot_release_capability.py")
text = capability.read_text(encoding="utf-8")
replacements = (
    (
        '"scripts/pilot_release_contract.py": "CAPABILITY_VERSION = 15\\n",',
        '"scripts/pilot_release_contract.py": "CAPABILITY_VERSION = 16\\n",',
    ),
    (
        '"SCHEMA_VERSION = 5\\nverified_admission_context(\\n"',
        '"SCHEMA_VERSION = 6\\ndatabase_evidence_contract\\nverified_admission_context(\\n"',
    ),
    (
        '"approved_operators(manifest)\\n"\n        "state.pilot_state_revision',
        '"approved_operators(manifest)\\n"\n'
        '        "validate_pilot_database_evidence(\\n"\n'
        '        "state.pilot_state_revision',
    ),
    (
        '"validate_anchor_transition(\\n"\n        "Stopped pilot runtime',
        '"validate_anchor_transition(\\n"\n'
        '        "validate_pilot_database_evidence(\\n"\n'
        '        "Stopped pilot runtime',
    ),
    (
        "assert CAPABILITY_VERSION == 15",
        "assert CAPABILITY_VERSION == 16",
    ),
)
for old, new in replacements:
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"Expected one capability fixture marker: {old[:100]!r}; found {count}"
        )
    text = text.replace(old, new, 1)

insertion = '''    "backend/services/pilot_database_evidence.py": (
        "def validate_pilot_database_evidence(): pass\\n"
        "pilot slot order_id\\nPostgreSQL payment\\nPostgreSQL refund\\n"
        "final GO scenario order IDs\\n"
    ),
    "backend/tests/test_pilot_database_evidence.py": (
        "test_exact_completed_twenty_order_database_evidence_is_accepted\\n"
        "test_missing_or_wrong_slot_order_fails_closed\\n"
        "test_payment_refund_status_and_amount_are_read_from_postgresql\\n"
        "test_final_go_rejects_active_or_incomplete_runtime\\n"
    ),
'''
anchor = '    "scripts/pilot_control_binding.py": ('
if text.count(anchor) != 1:
    raise SystemExit("Capability fixture insertion anchor is missing or duplicated")
text = text.replace(anchor, insertion + anchor, 1)
capability.write_text(text, encoding="utf-8")
