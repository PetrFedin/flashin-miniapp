from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def test_v46_post_launch_files_exist():
    for path in [
        "deploy/loadtest/k6_smoke.js",
        "scripts/performance_budget.py",
        "docs/post_launch/day_0_checklist.md",
        "docs/post_launch/day_7_review.md",
        "docs/post_launch/day_30_scale_plan.md",
        "docs/post_launch/kpi_dashboard_spec.md",
        "docs/templates/bug_report_template.md",
    ]:
        assert (ROOT / path).exists()
