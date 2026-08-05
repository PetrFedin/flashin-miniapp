from pathlib import Path

path = Path("backend/tests/test_pilot_release_capability.py")
content = path.read_text(encoding="utf-8")
old = (
    '"SCHEMA_VERSION = 6\\ndatabase_evidence_contract\\n'
    'verified_admission_context(\\n"'
)
new = (
    '"SCHEMA_VERSION = 7\\ndatabase_evidence_contract\\n"\n'
    '        "inventory_evidence_contract\\nverified_admission_context(\\n"'
)
if content.count(old) != 1:
    raise SystemExit(
        f"Expected one stale synthetic pilot state marker; found {content.count(old)}"
    )
path.write_text(content.replace(old, new, 1), encoding="utf-8")
