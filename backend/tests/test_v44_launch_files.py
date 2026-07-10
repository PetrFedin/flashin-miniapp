from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def test_v44_launch_scripts_exist():
    for path in [
        "scripts/setup_wizard.py",
        "scripts/check_integrations.py",
        "scripts/production_readiness_report.py",
        "scripts/generate_20_order_pilot_sheet.py",
        "docs/v44_launch_cockpit.md",
        "docs/v44_what_to_fill_before_launch.md",
    ]:
        assert (ROOT / path).exists()

def test_legal_templates_are_not_empty():
    for path in [
        "frontend/public/legal/offer.html",
        "frontend/public/legal/privacy.html",
        "frontend/public/legal/returns.html",
    ]:
        assert len((ROOT / path).read_text(encoding="utf-8")) > 500
