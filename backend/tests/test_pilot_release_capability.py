import shutil
import subprocess
import sys
from pathlib import Path

import pytest

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
    """Build the synthetic release from the real current capability surface.

    This intentionally avoids a hand-maintained copy of every marker. A new
    release capability must be proven by the exact files that production will
    package; marker-removal tests below mutate one copied file at a time.
    """

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "pilot@example.com")
    _git(repo, "config", "user.name", "Pilot Test")
    for relative in sorted(REQUIRED_FILES):
        source = ROOT / relative
        assert source.is_file(), f"Required release capability source is missing: {relative}"
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "guarded release")
    return repo


def _release(repo: Path, tmp_path: Path, release_id: str, created_at: str) -> Path:
    state = create_release(
        repo,
        tmp_path / "builds",
        release_id=release_id,
        created_at=created_at,
    )
    return Path(state["archive"])


def test_signed_release_capability_is_bound_to_exact_release():
    assert CAPABILITY_VERSION == 18
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


def test_immutable_archive_accepts_complete_capability_and_rejects_missing_file(tmp_path):
    repo = _guarded_repo(tmp_path)
    guarded = _release(repo, tmp_path, "guarded", "2026-08-10T00:00:00Z")
    assert inspect_runtime_guard(guarded) == []

    missing_path = repo / "scripts/backup_integrity.py"
    missing_path.unlink()
    _git(repo, "add", "-u")
    _git(repo, "commit", "-qm", "remove backup integrity")
    unguarded = _release(repo, tmp_path, "unguarded", "2026-08-10T00:01:00Z")
    errors = inspect_runtime_guard(unguarded)
    assert any("scripts/backup_integrity.py" in error for error in errors)


@pytest.mark.parametrize(
    ("path", "replacement", "expected_marker"),
    [
        ("backend/api/payments.py", "class ProviderPaymentIntegrityError: pass\n", "trip_pilot_circuit_breaker"),
        (".github/workflows/ci.yml", "jobs:\n  docker:\n    needs: [backend]\n", "browser-e2e"),
        ("backend/middleware/metrics.py", "def metrics_response(): pass\n", "flashin_pilot_metrics_collection_success"),
        ("admin/src/BusinessEventsPanel.jsx", "export default function Panel() {}\n", "ServiceOperationsPanel"),
        ("backend/api/support.py", "class AdminSupportTicketOut: pass\n", "assigned_admin_id"),
        ("admin/src/FulfillmentOperationsPanel.jsx", "export default function Panel() {}\n", "/api/fulfillment/tasks"),
        ("backend/services/loyalty.py", "def reward_referral_after_first_paid_order(): pass\n", "_lock_referral_customer"),
        ("scripts/backup_integrity.py", "KIND = 'broken'\n", "postgres_backup_manifest"),
        ("scripts/restore_postgres.sh", "#!/usr/bin/env bash\nexit 0\n", "verify-live"),
        ("scripts/deploy_release_gate.py", "#!/usr/bin/env python3\n", "retained under deploy/release/builds"),
        ("scripts/deploy_production.sh", "#!/usr/bin/env bash\n", "deploy_release_gate.py"),
        ("scripts/pilot_release_contract.py", "CAPABILITY_VERSION = 17\n", "CAPABILITY_VERSION = 18"),
    ],
)
def test_immutable_archive_rejects_removed_guard_marker(
    tmp_path,
    path,
    replacement,
    expected_marker,
):
    repo = _guarded_repo(tmp_path)
    target = repo / path
    target.write_text(replacement, encoding="utf-8")
    _git(repo, "add", path)
    _git(repo, "commit", "-qm", f"remove guard from {path}")

    release = _release(repo, tmp_path, "unwired", "2026-08-10T00:02:00Z")
    errors = inspect_runtime_guard(release)
    assert any(path in error for error in errors)
    assert any(expected_marker in error for error in errors)


def test_immutable_archive_rejects_missing_full_release_rollback_proof(tmp_path):
    repo = _guarded_repo(tmp_path)
    smoke = repo / "scripts/release_rollback_smoke.sh"
    smoke.write_text("ROLLBACK_DRILL=1\n", encoding="utf-8")
    _git(repo, "add", str(smoke.relative_to(repo)))
    _git(repo, "commit", "-qm", "remove full rollback proof")
    archive = _release(repo, tmp_path, "missing-full-rollback", "2026-08-10T00:03:00Z")

    errors = inspect_runtime_guard(archive)

    assert any("runtime_image_rebuilt" in error for error in errors)
    assert any("signed_evidence_verified" in error for error in errors)
