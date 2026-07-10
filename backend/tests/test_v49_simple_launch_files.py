from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def test_v49_launch_files_exist():
    for path in [
        "scripts/launch.py",
        "scripts/start_simple.sh",
        "scripts/connected_system_audit.py",
        "scripts/simplicity_score.py",
        "docs/v49_unified_system_map.md",
        "docs/v49_final_gap_analysis.md",
        "docs/v49_30_minute_launch_plan.md",
    ]:
        assert (ROOT / path).exists()
