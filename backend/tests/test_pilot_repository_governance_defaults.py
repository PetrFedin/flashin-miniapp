from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot_repository_governance import DEFAULT_REQUIRED_CHECKS, settings  # noqa: E402

EXPECTED_REQUIRED_CHECKS = (
    "backend",
    "frontend",
    "admin",
    "browser-e2e",
    "integrated-e2e",
    "docker",
)


def test_governance_defaults_require_full_ci_gate():
    env = {"PILOT_GITHUB_REPOSITORY": "PetrFedin/flashin-miniapp"}

    assert DEFAULT_REQUIRED_CHECKS == EXPECTED_REQUIRED_CHECKS
    assert settings(env)["required_checks"] == EXPECTED_REQUIRED_CHECKS
