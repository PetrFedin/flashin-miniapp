from scripts.pilot_evidence import sign_payload
from scripts.pilot_release_capability import capability_payload, validate_capability


def _release_state():
    return {
        "release_id": "release-guarded",
        "git_commit": "a" * 40,
        "sha256": "b" * 64,
    }


def test_signed_release_capability_is_bound_to_exact_release():
    secret = "s" * 48
    state = _release_state()
    state["capabilities"] = {
        "pilot_runtime_guard": sign_payload(capability_payload(state), secret)
    }

    assert validate_capability(state, secret) == []

    state["sha256"] = "c" * 64
    errors = validate_capability(state, secret)
    assert any("archive_sha256" in error for error in errors)


def test_unsigned_or_tampered_release_capability_is_rejected():
    secret = "s" * 48
    state = _release_state()
    assert validate_capability(state, secret)

    capability = sign_payload(capability_payload(state), secret)
    capability["version"] = 2
    state["capabilities"] = {"pilot_runtime_guard": capability}
    errors = validate_capability(state, secret)
    assert any("signature" in error for error in errors)
    assert any("version" in error for error in errors)
