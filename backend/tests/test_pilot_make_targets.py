from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")


def _recipe(target: str) -> str:
    marker = f"{target}:\n"
    assert marker in MAKEFILE
    tail = MAKEFILE.split(marker, 1)[1]
    lines: list[str] = []
    for line in tail.splitlines():
        if not line:
            if lines:
                break
            continue
        if line.startswith("\t"):
            lines.append(line.strip())
            continue
        break
    return "\n".join(lines)


def test_pilot_live_verify_is_read_only_composition():
    recipe = _recipe("pilot-live-verify")
    assert "$(MAKE) telegram-launch-check" in recipe
    assert "$(MAKE) pilot-gate" in recipe
    for unsafe in (
        "provider-probes",
        "--acknowledge-side-effects",
        "--acknowledge-provider-change",
        "--acknowledge-customer-provisioning",
    ):
        assert unsafe not in recipe


def test_telegram_mutations_require_operator_args():
    configure = _recipe("telegram-launch-configure")
    assert "python3 scripts/configure_telegram_launch_surface.py $(ARGS)" in configure
    assert "--acknowledge-provider-change" not in configure

    auth = _recipe("telegram-real-auth")
    assert "python3 scripts/telegram_real_auth_smoke.py $(ARGS)" in auth
    assert "--acknowledge-customer-provisioning" not in auth


def test_real_e2e_targets_use_guard_flags():
    assert _recipe("real-order-e2e") == (
        "RUN_REAL_E2E=1 python -m pytest -q "
        "backend/tests/e2e/test_real_order_flow_runner.py"
    )
    assert _recipe("real-lifecycle-e2e") == (
        "RUN_REAL_LIFECYCLE_E2E=1 python -m pytest -q "
        "backend/tests/e2e/test_order_payment_refund_flow.py"
    )


def test_final_admission_targets_require_p01_p20_checklist():
    assert _recipe("pilot-checklist-create") == (
        "python3 scripts/pilot_launch_checklist.py create $(ARGS)"
    )
    assert _recipe("pilot-checklist-status") == (
        "python3 scripts/pilot_launch_checklist.py verify $(ARGS)"
    )
    assert _recipe("pilot-checklist-attach") == (
        "python3 scripts/pilot_launch_admission.py attach $(ARGS)"
    )

    final_status = _recipe("pilot-admission-status")
    assert final_status == "python3 scripts/pilot_launch_admission.py verify $(ARGS)"
    assert "pilot_governance_admission.py verify" not in final_status


def test_runtime_arm_reverifies_final_admission_before_mutation():
    recipe = _recipe("pilot-runtime-arm")
    final_gate = "python3 scripts/pilot_launch_admission.py verify"
    arm = "python3 scripts/pilot_runtime.py arm $(ARGS)"
    assert final_gate in recipe
    assert arm in recipe
    assert recipe.index(final_gate) < recipe.index(arm)
    assert "pilot_launch_admission.py verify $(ARGS)" not in recipe
