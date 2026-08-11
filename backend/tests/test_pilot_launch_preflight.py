from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import pilot_launch_preflight as preflight  # noqa: E402


SHA = "a" * 40
RUNTIME_ENV = {
    "APP_ENV": "production",
    "PILOT_RUNTIME_ENFORCED": "true",
    "PILOT_RUNTIME_MAX_ORDERS": "20",
}


def _complete_dependencies(monkeypatch, tmp_path):
    release = {
        "release_id": "pilot-release",
        "git_commit": SHA,
        "sha256": "b" * 64,
        "archive": str(tmp_path / "deploy/release/builds/pilot.zip"),
    }
    monkeypatch.setattr(preflight, "read_env", lambda _path: dict(RUNTIME_ENV))
    monkeypatch.setattr(
        preflight,
        "load_verified_release_state",
        lambda _path: release,
    )
    monkeypatch.setattr(
        preflight,
        "verify_deploy_repository_provenance",
        lambda *_args, **_kwargs: {
            "ok": True,
            "branch_protected": True,
            "exact_push_ci_run_id": 4242,
            "errors": [],
        },
    )
    monkeypatch.setattr(
        preflight,
        "verify_deploy_release",
        lambda _root, _archive: {
            "ok": True,
            "git_commit": SHA,
            "errors": [],
        },
    )
    monkeypatch.setattr(preflight, "verify_admission_path", lambda *_args: [])
    monkeypatch.setattr(preflight, "_manifest_or_none", lambda _path: ({}, []))
    monkeypatch.setattr(preflight, "validate_attached_lifecycle", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(preflight, "validate_attached_governance", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        preflight,
        "validate_attached_launch_checklist",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(preflight, "verify_final_admission", lambda *_args: [])
    return release


def _stage(report, name):
    return next(item for item in report["stages"] if item["name"] == name)


def _completed_context(monkeypatch, tmp_path):
    context = tmp_path / "docs/pilot/evidence/real_order_e2e_context.json"
    context.parent.mkdir(parents=True)
    context.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        preflight,
        "inspect_context",
        lambda _path: {
            "ok": True,
            "phase": "payment_created",
            "requires_investigation": False,
            "order_id": 101,
            "variant_id": 202,
            "provider": "yookassa",
            "provider_payment_id": "provider-303",
            "errors": [],
        },
    )
    return context


def test_complete_preflight_only_means_ready_to_arm(monkeypatch, tmp_path):
    _complete_dependencies(monkeypatch, tmp_path)
    context = _completed_context(monkeypatch, tmp_path)

    report = preflight.run_preflight(root=tmp_path, context_path=context)

    assert report["go"] is True
    assert report["meaning"] == "ready_for_pilot_runtime_arm"
    assert report["phase"] == "runtime_arm"
    assert report["next_action"] == "make pilot-runtime-arm"
    assert all(item["status"] == "complete" for item in report["stages"])


def test_runtime_configuration_is_fail_closed(monkeypatch, tmp_path):
    _complete_dependencies(monkeypatch, tmp_path)
    context = _completed_context(monkeypatch, tmp_path)
    monkeypatch.setattr(
        preflight,
        "read_env",
        lambda _path: {
            "APP_ENV": "staging",
            "PILOT_RUNTIME_ENFORCED": "false",
            "PILOT_RUNTIME_MAX_ORDERS": "21",
        },
    )

    report = preflight.run_preflight(root=tmp_path, context_path=context)

    assert report["go"] is False
    assert report["phase"] == "runtime_configuration"
    stage = _stage(report, "runtime_configuration")
    assert stage["status"] == "blocked"
    assert any("APP_ENV must be production" in item for item in stage["errors"])
    assert any("PILOT_RUNTIME_ENFORCED must be true" in item for item in stage["errors"])
    assert any("exactly 20" in item for item in stage["errors"])


def test_missing_real_order_context_is_ready_for_flow_but_not_go(monkeypatch, tmp_path):
    _complete_dependencies(monkeypatch, tmp_path)
    context = tmp_path / "docs/pilot/evidence/real_order_e2e_context.json"
    monkeypatch.setattr(
        preflight,
        "inspect_context",
        lambda _path: {
            "ok": False,
            "phase": None,
            "requires_investigation": False,
            "errors": ["context is missing"],
        },
    )

    report = preflight.run_preflight(root=tmp_path, context_path=context)

    assert report["go"] is False
    assert report["phase"] == "real_order_context"
    context_stage = _stage(report, "real_order_context")
    assert context_stage["status"] == "ready"
    assert context_stage["errors"] == []
    assert "real-order-e2e" in context_stage["next_action"]


def test_interrupted_real_order_context_blocks_rerun(monkeypatch, tmp_path):
    _complete_dependencies(monkeypatch, tmp_path)
    context = tmp_path / "docs/pilot/evidence/real_order_e2e_context.json"
    context.parent.mkdir(parents=True)
    context.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        preflight,
        "inspect_context",
        lambda _path: {
            "ok": False,
            "phase": "checkout_intent",
            "requires_investigation": True,
            "order_id": None,
            "variant_id": 202,
            "provider": "yookassa",
            "provider_payment_id": None,
            "errors": [],
        },
    )

    report = preflight.run_preflight(root=tmp_path, context_path=context)

    assert report["go"] is False
    assert report["phase"] == "real_order_context"
    context_stage = _stage(report, "real_order_context")
    assert context_stage["status"] == "blocked"
    assert "real_provider_e2e_recovery.md" in context_stage["next_action"]
    assert any("must be reconciled" in item for item in context_stage["errors"])


def test_local_only_mode_can_never_authorize_runtime_arm(monkeypatch, tmp_path):
    _complete_dependencies(monkeypatch, tmp_path)
    context = _completed_context(monkeypatch, tmp_path)

    def forbidden_remote_call(*_args, **_kwargs):
        raise AssertionError("local-only mode must not call GitHub provenance")

    monkeypatch.setattr(
        preflight,
        "verify_deploy_repository_provenance",
        forbidden_remote_call,
    )

    report = preflight.run_preflight(
        root=tmp_path,
        context_path=context,
        local_only=True,
    )

    assert report["go"] is False
    assert report["phase"] == "repository_provenance"
    provenance = _stage(report, "repository_provenance")
    assert provenance["status"] == "blocked"
    assert any("cannot authorize pilot arm" in item for item in provenance["errors"])


def test_process_github_token_is_redacted_from_errors(monkeypatch, tmp_path):
    _complete_dependencies(monkeypatch, tmp_path)
    context = _completed_context(monkeypatch, tmp_path)
    secret = "runtime-secret-token-value"
    monkeypatch.setenv("GITHUB_TOKEN", secret)

    def failed_provenance(*_args, **kwargs):
        assert kwargs["token"] == secret
        return {
            "ok": False,
            "branch_protected": False,
            "exact_push_ci_run_id": None,
            "errors": [f"upstream rejected credential {secret}"],
        }

    monkeypatch.setattr(
        preflight,
        "verify_deploy_repository_provenance",
        failed_provenance,
    )

    report = preflight.run_preflight(root=tmp_path, context_path=context)
    rendered = str(report)

    assert report["go"] is False
    assert secret not in rendered
    assert "[redacted]" in rendered
