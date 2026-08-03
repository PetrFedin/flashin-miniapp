import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot_evidence import sign_payload  # noqa: E402
from pilot_release_capability import (  # noqa: E402
    CAPABILITY_VERSION,
    REQUIRED_FILES,
    capability_payload,
    inspect_runtime_guard,
    validate_capability,
)
from release_control import create_release  # noqa: E402


def _release_state():
    return {
        "release_id": "release-guarded",
        "git_commit": "a" * 40,
        "sha256": "b" * 64,
    }


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    )


def _guarded_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "pilot@example.com")
    _git(repo, "config", "user.name", "Pilot Test")
    for relative in REQUIRED_FILES:
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        content = "guarded\n"
        if relative == "backend/api/orders.py":
            content = "acquire_pilot_checkout()\nrecord_pilot_order()\n"
        elif relative == "backend/services/pilot_circuit_breaker.py":
            content = (
                "def stop_pilot_for_order():\n"
                "    pass\n"
                "def trip_pilot_circuit_breaker():\n"
                "    pass\n"
            )
        elif relative == "backend/api/payments.py":
            content = (
                "class ProviderPaymentIntegrityError: pass\n"
                "trip_pilot_circuit_breaker()\n"
                "stop_pilot_for_order()\n"
            )
        elif relative == "backend/api/returns.py":
            content = "trip_pilot_circuit_breaker()\nstop_pilot_for_order()\n"
        elif relative == "backend/services/payment_reconciliation.py":
            content = "payment_reconciliation_mismatch\nstop_pilot_for_order()\n"
        elif relative == "docker-compose.production.yml":
            content = "./docs:/app/docs:ro\n./deploy/release:/app/deploy/release:ro\n"
        elif relative == "scripts/deploy_production.sh":
            content = "pilot_runtime.py _stop\ncheck_pilot_runtime_integrity.py\n"
        elif relative == "scripts/rollback.sh":
            content = (
                'CAPABILITY_SCRIPT="scripts/pilot_release_capability.py"\n'
                'python3 "$CAPABILITY_SCRIPT" inspect --archive "$RELEASE"\n'
                "pilot_runtime.py _stop\n"
                "check_pilot_runtime_integrity.py\n"
            )
        path.write_text(content, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "guarded release")
    return repo


def test_signed_release_capability_is_bound_to_exact_release():
    assert CAPABILITY_VERSION == 2
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
    capability["version"] = 99
    state["capabilities"] = {"pilot_runtime_guard": capability}
    errors = validate_capability(state, secret)
    assert any("signature" in error for error in errors)
    assert any("version" in error for error in errors)


def test_immutable_archive_inspection_accepts_guarded_release_and_rejects_missing_file(tmp_path):
    repo = _guarded_repo(tmp_path)
    guarded = create_release(
        repo,
        tmp_path / "builds",
        release_id="guarded",
        created_at="2026-08-03T19:00:00Z",
    )
    assert inspect_runtime_guard(Path(guarded["archive"])) == []

    missing_path = repo / "backend/services/pilot_circuit_breaker.py"
    missing_path.unlink()
    _git(repo, "add", "-u")
    _git(repo, "commit", "-qm", "remove payment circuit breaker")
    unguarded = create_release(
        repo,
        tmp_path / "builds",
        release_id="unguarded",
        created_at="2026-08-03T19:01:00Z",
    )
    errors = inspect_runtime_guard(Path(unguarded["archive"]))
    assert any("backend/services/pilot_circuit_breaker.py" in error for error in errors)


def test_immutable_archive_inspection_rejects_unwired_payment_breaker(tmp_path):
    repo = _guarded_repo(tmp_path)
    payments = repo / "backend/api/payments.py"
    payments.write_text("class ProviderPaymentIntegrityError: pass\n", encoding="utf-8")
    _git(repo, "add", str(payments.relative_to(repo)))
    _git(repo, "commit", "-qm", "remove payment breaker wiring")

    release = create_release(
        repo,
        tmp_path / "builds",
        release_id="unwired",
        created_at="2026-08-03T19:02:00Z",
    )
    errors = inspect_runtime_guard(Path(release["archive"]))
    assert any("backend/api/payments.py" in error for error in errors)
    assert any("trip_pilot_circuit_breaker" in error for error in errors)
