from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one occurrence, found {count}: {old[:120]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


admission = Path("scripts/pilot_admission.py")
text = admission.read_text(encoding="utf-8")
helper = '''def validate_admission_evidence_inputs(
    provider_report: Mapping[str, Any],
    live_report: Mapping[str, Any],
    rollback_report: Mapping[str, Any],
    *,
    env: Mapping[str, str],
    current_release: Mapping[str, Any],
    provider_max_age_minutes: int,
    live_max_age_minutes: int,
    rollback_max_age_days: int,
) -> list[str]:
    """Validate the exact evidence set before an admission manifest is created."""
    errors: list[str] = []
    errors.extend(
        validate_provider_report(
            provider_report,
            env=env,
            current_release=current_release,
            max_age_minutes=provider_max_age_minutes,
        )
    )
    errors.extend(
        validate_live_gate_report(
            live_report,
            env=env,
            current_release=current_release,
            max_age_minutes=live_max_age_minutes,
        )
    )
    errors.extend(
        validate_rollback_drill_report(
            rollback_report,
            env=env,
            max_age_days=rollback_max_age_days,
        )
    )
    return list(dict.fromkeys(errors))


'''
anchor = "def render_markdown(manifest: Mapping[str, Any]) -> str:\n"
if text.count(anchor) != 1:
    raise SystemExit("pilot_admission.py render anchor changed unexpectedly")
text = text.replace(anchor, helper + anchor, 1)
old = '''        preflight_errors: list[str] = []
        preflight_errors.extend(
            validate_provider_report(
                provider,
                env=env,
                current_release=current,
                max_age_minutes=settings["provider"],
            )
        )
        preflight_errors.extend(
            validate_live_gate_report(live, max_age_minutes=settings["live"])
        )
        preflight_errors.extend(
            validate_rollback_drill_report(
                rollback,
                env=env,
                max_age_days=settings["rollback"],
            )
        )
'''
new = '''        preflight_errors = validate_admission_evidence_inputs(
            provider,
            live,
            rollback,
            env=env,
            current_release=current,
            provider_max_age_minutes=settings["provider"],
            live_max_age_minutes=settings["live"],
            rollback_max_age_days=settings["rollback"],
        )
'''
if text.count(old) != 1:
    raise SystemExit("pilot_admission.py create preflight block changed unexpectedly")
admission.write_text(text.replace(old, new, 1), encoding="utf-8")


tests = Path("backend/tests/test_pilot_admission.py")
text = tests.read_text(encoding="utf-8")
text = text.replace(
    "    validate_admission_manifest,\n    validate_live_gate_report,\n",
    "    validate_admission_evidence_inputs,\n"
    "    validate_admission_manifest,\n"
    "    validate_live_gate_report,\n",
    1,
)
text += '''


def test_admission_create_preflight_binds_live_gate_to_current_release(tmp_path: Path):
    values = env()
    current = release("current", "a")
    previous = release("previous", "b")
    unrelated = release("unrelated", "c")
    now = datetime.now(UTC)
    backup = tmp_path / "backup.sql.gz"
    backup.write_bytes(b"backup")
    provider = provider_report(now, values, current)
    live = live_report(now, values, current)
    rollback = build_rollback_drill_report(
        from_release=current,
        to_release=previous,
        backup_path=backup,
        env=values,
        completed_at=now,
        max_age_days=30,
    )

    assert validate_admission_evidence_inputs(
        provider,
        live,
        rollback,
        env=values,
        current_release=current,
        provider_max_age_minutes=60,
        live_max_age_minutes=30,
        rollback_max_age_days=30,
    ) == []

    unrelated_live = live_report(now, values, unrelated)
    errors = validate_admission_evidence_inputs(
        provider,
        unrelated_live,
        rollback,
        env=values,
        current_release=current,
        provider_max_age_minutes=60,
        live_max_age_minutes=30,
        rollback_max_age_days=30,
    )
    assert any("live gate release" in item for item in errors)
'''
tests.write_text(text, encoding="utf-8")


capability = Path("scripts/pilot_release_capability.py")
text = capability.read_text(encoding="utf-8")
old = '            _require_markers(bundle, files, "scripts/pilot_admission.py", ("live gate evidence signature is invalid", "live gate configuration fingerprint does not match", "live gate release binding is missing", "validate_release_binding(release, current_release)"), errors)\n'
new = '            _require_markers(bundle, files, "scripts/pilot_admission.py", ("live gate evidence signature is invalid", "live gate configuration fingerprint does not match", "live gate release binding is missing", "validate_release_binding(release, current_release)", "def validate_admission_evidence_inputs(", "current_release=current_release"), errors)\n'
if text.count(old) != 1:
    raise SystemExit("pilot_release_capability.py admission marker changed unexpectedly")
text = text.replace(old, new, 1)
old = '            _require_markers(bundle, files, "backend/tests/test_pilot_admission.py", ("test_live_gate_rejects_tampering_configuration_and_other_release", "configuration fingerprint", "live gate release"), errors)\n'
new = '            _require_markers(bundle, files, "backend/tests/test_pilot_admission.py", ("test_live_gate_rejects_tampering_configuration_and_other_release", "test_admission_create_preflight_binds_live_gate_to_current_release", "configuration fingerprint", "live gate release"), errors)\n'
if text.count(old) != 1:
    raise SystemExit("pilot_release_capability.py admission test marker changed unexpectedly")
capability.write_text(text.replace(old, new, 1), encoding="utf-8")


capability_tests = Path("backend/tests/test_pilot_release_capability.py")
text = capability_tests.read_text(encoding="utf-8")
old = '''    "scripts/pilot_admission.py": (
        'live gate evidence signature is invalid\\n'
        'live gate configuration fingerprint does not match\\n'
        'live gate release binding is missing\\n'
        'validate_release_binding(release, current_release)\\n'
    ),
'''
new = '''    "scripts/pilot_admission.py": (
        'live gate evidence signature is invalid\\n'
        'live gate configuration fingerprint does not match\\n'
        'live gate release binding is missing\\n'
        'validate_release_binding(release, current_release)\\n'
        'def validate_admission_evidence_inputs(): pass\\n'
        'current_release=current_release\\n'
    ),
'''
if text.count(old) != 1:
    raise SystemExit("capability synthetic admission fixture changed unexpectedly")
text = text.replace(old, new, 1)
old = '''    "backend/tests/test_pilot_admission.py": (
        'test_live_gate_rejects_tampering_configuration_and_other_release\\n'
        'configuration fingerprint\\nlive gate release\\n'
    ),
'''
new = '''    "backend/tests/test_pilot_admission.py": (
        'test_live_gate_rejects_tampering_configuration_and_other_release\\n'
        'test_admission_create_preflight_binds_live_gate_to_current_release\\n'
        'configuration fingerprint\\nlive gate release\\n'
    ),
'''
if text.count(old) != 1:
    raise SystemExit("capability synthetic admission test fixture changed unexpectedly")
capability_tests.write_text(text.replace(old, new, 1), encoding="utf-8")


matrix = Path("docs/pilot/end_to_end_coverage_matrix.md")
text = matrix.read_text(encoding="utf-8")
needle = "The live gate report is HMAC-signed and bound to the exact current release and configuration fingerprint before human admission can be created."
replacement = needle + " The admission create path validates that signed binding before it writes the manifest."
if text.count(needle) != 1:
    raise SystemExit("E2E matrix live gate sentence changed unexpectedly")
matrix.write_text(text.replace(needle, replacement, 1), encoding="utf-8")
