from argparse import Namespace
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import pilot_admission  # noqa: E402
import pilot_launch_preflight  # noqa: E402
import pilot_runtime  # noqa: E402


def test_direct_host_arm_blocks_before_runtime_mutation_when_preflight_is_no_go(
    monkeypatch,
    capsys,
):
    calls = []

    def blocked_preflight(*, root):
        calls.append(("preflight", root))
        return {
            "go": False,
            "meaning": "not_ready_for_pilot_runtime_arm",
            "phase": "repository_provenance",
            "next_action": "protect main",
            "stages": [],
        }

    monkeypatch.setattr(pilot_launch_preflight, "run_preflight", blocked_preflight)
    monkeypatch.setattr(
        pilot_runtime,
        "_compose_exec",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("runtime mutation must not be reached")
        ),
    )
    monkeypatch.setattr(
        pilot_admission,
        "verify_default_admission",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("baseline admission must not run after a blocked preflight")
        ),
    )

    exit_code = pilot_runtime._host_arm(
        Namespace(telegram_id=["123456789"], resume=False)
    )

    assert exit_code == 1
    assert calls == [("preflight", pilot_runtime.ROOT)]
    output = capsys.readouterr().out
    assert "Pilot launch preflight is not GO" in output
    assert "repository_provenance" in output


def test_direct_host_arm_checks_preflight_before_baseline_admission(
    monkeypatch,
):
    calls = []

    def allowed_preflight(*, root):
        calls.append(("preflight", root))
        return {"go": True, "stages": []}

    def blocked_baseline(root):
        calls.append(("baseline", root))
        return ["synthetic baseline block"]

    monkeypatch.setattr(pilot_launch_preflight, "run_preflight", allowed_preflight)
    monkeypatch.setattr(pilot_admission, "verify_default_admission", blocked_baseline)
    monkeypatch.setattr(
        pilot_runtime,
        "_compose_exec",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("runtime mutation must not be reached")
        ),
    )

    exit_code = pilot_runtime._host_arm(
        Namespace(telegram_id=["123456789"], resume=False)
    )

    assert exit_code == 1
    assert calls == [
        ("preflight", pilot_runtime.ROOT),
        ("baseline", pilot_runtime.ROOT),
    ]
