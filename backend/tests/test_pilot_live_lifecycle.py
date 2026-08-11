import json
from datetime import UTC, datetime
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot_control_binding import build_admission_binding  # noqa: E402
from pilot_evidence import (  # noqa: E402
    configuration_fingerprint,
    sha256_file,
    sign_payload,
)
from pilot_lifecycle_admission import (  # noqa: E402
    ORDER_CONTEXT_EVIDENCE_PATH,
    ORDER_CORRELATED_SCENARIOS,
    validate_attached_lifecycle,
    validate_order_lifecycle_correlation,
)
from pilot_live_lifecycle import (  # noqa: E402
    BASE_REQUIRED_SCENARIOS,
    build_report,
    required_scenarios,
    validate_live_lifecycle_report,
)


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
SECRET = "pilot-lifecycle-secret-0123456789abcdef"
RELEASE = {
    "release_id": "release-live",
    "git_commit": "a" * 40,
    "sha256": "b" * 64,
    "promoted_at": "2026-08-06T11:00:00Z",
}
APPROVALS = {
    "business_owner": "Business",
    "operations_owner": "Operations",
    "technical_owner": "Technical",
    "legal_owner": "Legal",
    "support_owner": "Support",
}


def _env(**overrides):
    values = {
        "APP_ENV": "production",
        "API_PUBLIC_URL": "https://api.flashin.example",
        "MINI_APP_URL": "https://mini.flashin.example",
        "ADMIN_URL": "https://admin.flashin.example",
        "PILOT_EVIDENCE_SIGNING_SECRET": SECRET,
        "MEILISEARCH_ENABLED": "false",
        "MEDIA_STORAGE": "local",
    }
    values.update(overrides)
    return values


def _write_env(root: Path, env):
    (root / ".env").write_text(
        "\n".join(f"{key}={value}" for key, value in env.items()) + "\n",
        encoding="utf-8",
    )


def _paths(root: Path):
    pilot_dir = root / "docs/pilot"
    evidence_dir = pilot_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    return pilot_dir, evidence_dir


def _input(root: Path, env, *, owner="Operations", notes="controlled live observation"):
    _pilot_dir, evidence_dir = _paths(root)
    evidence = evidence_dir / "evidence.txt"
    evidence.write_text("FLASHIN controlled pilot evidence\n", encoding="utf-8")
    context = evidence_dir / "real_order_e2e_context.json"
    context.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "flashin_real_order_e2e_context",
                "created_at": "2026-08-06T12:00:00Z",
                "api_base": env["API_PUBLIC_URL"],
                "subject_id": "order:4242",
                "order_id": 4242,
                "product_id": 7,
                "variant_id": 9,
                "quantity": 1,
                "baseline_stock_qty": 5,
                "baseline_reserved_qty": 0,
                "provider": "yookassa",
                "provider_payment_id": "payment-4242",
            }
        ),
        encoding="utf-8",
    )
    scenarios = []
    for index, name in enumerate(required_scenarios(env), start=1):
        scenario_evidence = [
            {
                "label": "sanitized operator evidence",
                "path": str(evidence),
            }
        ]
        if name in ORDER_CORRELATED_SCENARIOS:
            scenario_evidence.append(
                {
                    "label": "shared real-order E2E context",
                    "path": str(context),
                }
            )
        scenarios.append(
            {
                "name": name,
                "status": "PASS",
                "observed_at": "2026-08-06T12:00:00Z",
                "owner": owner,
                "subject_id": (
                    "order:4242"
                    if name in ORDER_CORRELATED_SCENARIOS
                    else f"subject-{index}"
                ),
                "notes": notes,
                "evidence": scenario_evidence,
            }
        )
    return {"scenarios": scenarios}, evidence


def _report(root: Path, env, **input_kwargs):
    payload, evidence = _input(root, env, **input_kwargs)
    report = build_report(
        payload,
        root=root,
        env=env,
        current_release=RELEASE,
        max_age_hours=24,
        now=NOW,
    )
    return report, evidence


def _manifest(env, report_path: Path, *, attached=True):
    evidence = {}
    acknowledgements = {}
    if attached:
        evidence["live_lifecycle_report"] = {
            "path": "docs/pilot/live_lifecycle_report.json",
            "sha256": sha256_file(report_path),
        }
        acknowledgements["live_lifecycle_completed"] = True
    payload = {
        "schema_version": 1,
        "kind": "pilot_admission",
        "decision": "GO",
        "created_at": "2026-08-06T12:00:00Z",
        "expires_at": "2026-08-06T13:00:00Z",
        "configuration_fingerprint": configuration_fingerprint(env, SECRET),
        "release": RELEASE,
        "approvals": APPROVALS,
        "acknowledgements": acknowledgements,
        "evidence": evidence,
    }
    return sign_payload(payload, SECRET)


def test_live_lifecycle_report_requires_exact_deployed_scenarios_and_hashes(tmp_path):
    env = _env()
    report, evidence = _report(tmp_path, env)

    assert set(BASE_REQUIRED_SCENARIOS) == set(required_scenarios(env))
    assert validate_live_lifecycle_report(
        report,
        root=tmp_path,
        env=env,
        expected_release=RELEASE,
        max_age_hours=24,
        now=NOW,
    ) == []
    assert validate_order_lifecycle_correlation(report, root=tmp_path) == []
    order_scenarios = [
        scenario
        for scenario in report["scenarios"]
        if scenario["name"] in ORDER_CORRELATED_SCENARIOS
    ]
    assert order_scenarios
    context_hashes = set()
    for scenario in order_scenarios:
        context_entries = [
            item
            for item in scenario["evidence"]
            if item["path"] == ORDER_CONTEXT_EVIDENCE_PATH
        ]
        assert len(context_entries) == 1
        context_hashes.add(context_entries[0]["sha256"])
    assert len(context_hashes) == 1
    assert all(
        item["path"].startswith("docs/pilot/evidence/")
        for scenario in report["scenarios"]
        for item in scenario["evidence"]
    )

    evidence.write_text("changed evidence\n", encoding="utf-8")
    errors = validate_live_lifecycle_report(
        report,
        root=tmp_path,
        env=env,
        expected_release=RELEASE,
        max_age_hours=24,
        now=NOW,
    )
    assert any("checksum does not match" in error for error in errors)


def test_order_linked_scenarios_must_share_one_controlled_subject(tmp_path):
    env = _env()
    _write_env(tmp_path, env)
    report, _ = _report(tmp_path, env)
    mismatched = json.loads(json.dumps(report))
    mismatched.pop("signature")
    refund = next(
        scenario
        for scenario in mismatched["scenarios"]
        if scenario["name"] == "yookassa_refund"
    )
    refund["subject_id"] = "order:9999"
    mismatched = sign_payload(mismatched, SECRET)

    errors = validate_order_lifecycle_correlation(mismatched, root=tmp_path)
    assert any("share one controlled-order subject_id" in error for error in errors)
    assert any("context subject_id does not match lifecycle" in error for error in errors)

    pilot_dir, _ = _paths(tmp_path)
    report_path = pilot_dir / "live_lifecycle_report.json"
    report_path.write_text(json.dumps(mismatched), encoding="utf-8")
    manifest = _manifest(env, report_path, attached=True)
    manifest_path = pilot_dir / "pilot_admission_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    attached_errors = validate_attached_lifecycle(
        manifest_path,
        manifest,
        env=env,
        root=tmp_path,
        max_age_hours=24,
        now=NOW,
    )
    assert any("share one controlled-order subject_id" in error for error in attached_errors)


def test_order_linked_scenario_missing_shared_context_is_rejected(tmp_path):
    env = _env()
    report, _ = _report(tmp_path, env)
    refund = next(
        scenario
        for scenario in report["scenarios"]
        if scenario["name"] == "yookassa_refund"
    )
    refund["evidence"] = [
        item
        for item in refund["evidence"]
        if item["path"] != ORDER_CONTEXT_EVIDENCE_PATH
    ]

    errors = validate_order_lifecycle_correlation(report, root=tmp_path)
    assert any("must reference exactly one shared real-order E2E context" in error for error in errors)


def test_conditional_search_and_media_scenarios_are_required(tmp_path):
    env = _env(MEILISEARCH_ENABLED="true", MEDIA_STORAGE="r2")
    payload, _evidence = _input(tmp_path, _env())

    with pytest.raises(ValueError, match="missing lifecycle scenarios"):
        build_report(
            payload,
            root=tmp_path,
            env=env,
            current_release=RELEASE,
            max_age_hours=24,
            now=NOW,
        )

    report, _ = _report(tmp_path, env)
    assert set(required_scenarios(env)) == {
        *BASE_REQUIRED_SCENARIOS,
        "meilisearch_live_index",
        "media_live_delivery",
    }
    assert validate_live_lifecycle_report(
        report,
        root=tmp_path,
        env=env,
        expected_release=RELEASE,
        max_age_hours=24,
        now=NOW,
    ) == []
    assert validate_order_lifecycle_correlation(report, root=tmp_path) == []


def test_tampering_staleness_and_raw_init_data_fail_closed(tmp_path):
    env = _env()
    report, _evidence = _report(tmp_path, env)
    tampered = json.loads(json.dumps(report))
    tampered["scenarios"][0]["subject_id"] = "another-payment"
    errors = validate_live_lifecycle_report(
        tampered,
        root=tmp_path,
        env=env,
        expected_release=RELEASE,
        max_age_hours=24,
        now=NOW,
    )
    assert "live lifecycle evidence signature is invalid" in errors

    stale = json.loads(json.dumps(report))
    stale.pop("signature")
    stale["created_at"] = "2026-08-04T10:00:00Z"
    stale["expires_at"] = "2026-08-05T10:00:00Z"
    stale["scenarios"][0]["observed_at"] = "2026-08-04T10:00:00Z"
    stale = sign_payload(stale, SECRET)
    errors = validate_live_lifecycle_report(
        stale,
        root=tmp_path,
        env=env,
        expected_release=RELEASE,
        max_age_hours=24,
        now=NOW,
    )
    assert any("expired" in error or "older" in error for error in errors)

    payload, _ = _input(
        tmp_path,
        env,
        notes="auth_date=1&query_id=secret&user=%7B1%7D&hash=raw",
    )
    with pytest.raises(ValueError, match="raw Telegram init data"):
        build_report(
            payload,
            root=tmp_path,
            env=env,
            current_release=RELEASE,
            max_age_hours=24,
            now=NOW,
        )


def test_evidence_symlink_and_outside_repository_are_rejected(tmp_path):
    env = _env()
    _pilot_dir, evidence_dir = _paths(tmp_path)
    target = evidence_dir / "target.txt"
    target.write_text("safe\n", encoding="utf-8")
    link = evidence_dir / "link.txt"
    link.symlink_to(target)
    payload, _ = _input(tmp_path, env)
    payload["scenarios"][0]["evidence"][0]["path"] = str(link)
    with pytest.raises(ValueError, match="must not be a symlink"):
        build_report(
            payload,
            root=tmp_path,
            env=env,
            current_release=RELEASE,
            max_age_hours=24,
            now=NOW,
        )

    outside = tmp_path.parent / "outside-evidence.txt"
    outside.write_text("safe\n", encoding="utf-8")
    payload, _ = _input(tmp_path, env)
    payload["scenarios"][0]["evidence"][0]["path"] = str(outside)
    with pytest.raises(ValueError, match="inside the pilot repository root"):
        build_report(
            payload,
            root=tmp_path,
            env=env,
            current_release=RELEASE,
            max_age_hours=24,
            now=NOW,
        )


def test_go_admission_binding_requires_attached_live_lifecycle(tmp_path):
    env = _env()
    _write_env(tmp_path, env)
    report, _ = _report(tmp_path, env)
    pilot_dir, _ = _paths(tmp_path)
    report_path = pilot_dir / "live_lifecycle_report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    manifest_path = pilot_dir / "pilot_admission_manifest.json"
    missing = _manifest(env, report_path, attached=False)
    manifest_path.write_text(json.dumps(missing), encoding="utf-8")
    with pytest.raises(ValueError, match="live lifecycle evidence is invalid"):
        build_admission_binding(manifest_path, missing, root=tmp_path, now=NOW)

    attached = _manifest(env, report_path, attached=True)
    manifest_path.write_text(json.dumps(attached), encoding="utf-8")
    assert validate_attached_lifecycle(
        manifest_path,
        attached,
        env=env,
        root=tmp_path,
        max_age_hours=24,
        now=NOW,
    ) == []
    binding = build_admission_binding(manifest_path, attached, root=tmp_path, now=NOW)
    assert binding["live_lifecycle_report_sha256"] == sha256_file(report_path)


def test_live_lifecycle_owner_must_match_signed_admission_owner(tmp_path):
    env = _env()
    _write_env(tmp_path, env)
    report, _ = _report(tmp_path, env, owner="Unknown Operator")
    pilot_dir, _ = _paths(tmp_path)
    report_path = pilot_dir / "live_lifecycle_report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    manifest = _manifest(env, report_path, attached=True)
    manifest_path = pilot_dir / "pilot_admission_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    errors = validate_attached_lifecycle(
        manifest_path,
        manifest,
        env=env,
        root=tmp_path,
        max_age_hours=24,
        now=NOW,
    )
    assert any("not a signed admission owner" in error for error in errors)
