from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def test_v50_handover_files_exist():
    for path in [
        "scripts/generate_env_todo.py",
        "scripts/pilot_runner.py",
        "scripts/generate_release_pack.py",
        "docs/handover/first_run_error_map.md",
        "docs/handover/operator_handover_1_day.md",
        "docs/handover/developer_handover_addendum.md",
        "docs/v50_final_handover.md",
    ]:
        assert (ROOT / path).exists()
