import ast
import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import configure_mappers

from backend import main
from backend.database import utcnow_naive
from backend.models import Order


ROOT = Path(__file__).resolve().parents[2]
LEGACY_UTCNOW_BASELINE = {
    "backend/api/admin.py": 1,
    "backend/models.py": 54,
    "scripts/generate_release_pack.py": 1,
    "scripts/pilot_runner.py": 1,
    "scripts/release_freeze.py": 1,
}


def test_utcnow_naive_is_explicit_utc_and_matches_existing_db_contract():
    before = datetime.now(UTC).replace(tzinfo=None)
    value = utcnow_naive()
    after = datetime.now(UTC).replace(tzinfo=None)

    assert value.tzinfo is None
    assert before <= value <= after


def test_legacy_model_defaults_are_routed_through_utc_helper():
    configure_mappers()

    default = Order.__table__.c.created_at.default

    assert default is not None
    assert default.arg is utcnow_naive
    assert default.arg(None).tzinfo is None


def test_application_initializer_closes_database_session(monkeypatch):
    calls = []

    class FakeDb:
        def close(self):
            calls.append("close")

    db = FakeDb()
    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(use_create_all=False, enable_seed=True),
    )
    monkeypatch.setattr(main, "SessionLocal", lambda: db)
    monkeypatch.setattr(main, "bootstrap_admin", lambda value: calls.append(("bootstrap", value)))
    monkeypatch.setattr(main, "seed_products", lambda value: calls.append(("seed", value)))

    main.initialize_application()

    assert calls == [("bootstrap", db), ("seed", db), "close"]


def test_application_initializer_closes_database_session_on_failure(monkeypatch):
    calls = []

    class FakeDb:
        def close(self):
            calls.append("close")

    db = FakeDb()
    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(use_create_all=False, enable_seed=False),
    )

    def fail(_db):
        raise RuntimeError("bootstrap failed")

    monkeypatch.setattr(main, "bootstrap_admin", fail)

    with pytest.raises(RuntimeError, match="bootstrap failed"):
        main.initialize_application()

    assert calls == ["close"]


def test_lifespan_initializes_application_once(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "initialize_application", lambda: calls.append("initialize"))

    async def exercise_lifespan():
        async with main.lifespan(main.app):
            assert calls == ["initialize"]

    asyncio.run(exercise_lifespan())
    assert calls == ["initialize"]


def test_main_uses_lifespan_instead_of_deprecated_startup_hook():
    source = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")

    assert "lifespan=lifespan" in source
    assert ".on_event(" not in source


def test_datetime_utcnow_legacy_debt_is_explicitly_bounded():
    occurrences = {}
    for directory in ("backend", "bot", "scripts"):
        for path in sorted((ROOT / directory).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            count = sum(
                1
                for node in ast.walk(tree)
                if isinstance(node, ast.Attribute)
                and node.attr == "utcnow"
                and isinstance(node.value, ast.Name)
                and node.value.id == "datetime"
            )
            if count:
                occurrences[path.relative_to(ROOT).as_posix()] = count

    assert occurrences == LEGACY_UTCNOW_BASELINE
