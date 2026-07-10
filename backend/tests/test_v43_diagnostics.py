def test_diagnostics_imports():
    from backend.services.diagnostics import run_diagnostics
    assert callable(run_diagnostics)

def test_openapi_script_exists():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    assert (root / "scripts" / "generate_openapi_snapshot.py").exists()
