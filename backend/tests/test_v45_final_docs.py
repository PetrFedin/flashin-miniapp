from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def test_v45_docs_exist():
    for path in [
        "docs/v45_launch_command_center.md",
        "docs/v45_master_launch_checklist.md",
        "docs/v45_final_acceptance.md",
        "docs/incident_templates/payment_incident.md",
        "docs/sop/support_sop.md",
        "scripts/readiness_gate.py",
    ]:
        assert (ROOT / path).exists()
