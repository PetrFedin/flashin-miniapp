from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def test_v51_pilot_freeze_files_exist():
    for path in [
        "scripts/release_freeze.py",
        "scripts/pilot_evidence_log.py",
        "docs/acceptance/pilot_acceptance_signoff.md",
        "docs/acceptance/order_payment_stock_reconciliation_sheet.md",
        "docs/acceptance/what_not_to_touch_before_pilot.md",
        "docs/v51_pilot_freeze_layer.md",
    ]:
        assert (ROOT / path).exists()
