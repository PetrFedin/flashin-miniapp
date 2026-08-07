import subprocess
import sys
from pathlib import Path


def test_provider_integration_spine_transaction_smoke():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "scripts/provider_integration_spine_smoke.py"],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, (
        "provider integration spine smoke failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
