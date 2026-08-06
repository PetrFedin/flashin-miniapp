from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import pilot_governance_admission as governance  # noqa: E402
import pilot_lifecycle_admission as lifecycle  # noqa: E402


def test_lifecycle_attach_validates_the_exact_operator_manifest(tmp_path, monkeypatch):
    manifest = tmp_path / "custom-lifecycle-admission.json"
    report = tmp_path / "lifecycle-report.json"
    seen: list[tuple[Path, Path]] = []

    def reject_exact(path: Path, root: Path):
        seen.append((path, root))
        return ["custom manifest is invalid"]

    monkeypatch.setattr(lifecycle, "verify_admission_path", reject_exact)

    with pytest.raises(ValueError, match="custom manifest is invalid"):
        lifecycle.attach_lifecycle_report(manifest, report, root=tmp_path)

    assert seen == [(manifest, tmp_path)]


def test_governance_attach_validates_the_exact_operator_manifest(tmp_path, monkeypatch):
    manifest = tmp_path / "custom-governance-admission.json"
    report = tmp_path / "governance-report.json"
    seen: list[tuple[Path, Path]] = []

    def reject_exact(path: Path, root: Path):
        seen.append((path, root))
        return ["custom manifest is invalid"]

    monkeypatch.setattr(governance, "verify_admission_path", reject_exact)

    with pytest.raises(ValueError, match="custom manifest is invalid"):
        governance.attach_governance_report(manifest, report, root=tmp_path)

    assert seen == [(manifest, tmp_path)]
