from types import SimpleNamespace

import pytest

from backend.models import AdminUser
from backend.security import verify_password
from scripts import seed_admin


STRONG_PASSWORD = "Correct-Horse-2026!"


class FakeQuery:
    def __init__(self, values):
        self.values = values

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def with_for_update(self):
        return self

    def all(self):
        return list(self.values)


class FakeDb:
    def __init__(self, admins=None):
        self.admins = list(admins or [])
        self.added = []
        self.flushes = 0

    def query(self, model):
        assert model is AdminUser
        return FakeQuery(self.admins)

    def add(self, row):
        self.added.append(row)
        if isinstance(row, AdminUser):
            if row.id is None:
                row.id = 100 + len(self.admins)
            self.admins.append(row)

    def flush(self):
        self.flushes += 1


def test_empty_admin_table_creates_exactly_one_owner(monkeypatch):
    db = FakeDb()
    audit = []
    monkeypatch.setattr(
        seed_admin,
        "log_admin_action",
        lambda db, admin, action, entity_type, entity_id, payload: audit.append(
            (action, entity_type, entity_id, payload)
        ),
    )

    admin, created = seed_admin.seed_first_admin(
        db,
        email="Owner@Flashin.Store",
        password=STRONG_PASSWORD,
    )

    assert created is True
    assert len(db.admins) == 1
    assert admin.email == "owner@flashin.store"
    assert admin.role == "owner"
    assert admin.active is True
    assert verify_password(STRONG_PASSWORD, admin.password_hash) is True
    assert db.flushes == 1
    assert audit == [
        (
            "admin.bootstrap.create",
            "admin_user",
            admin.id,
            {"role": "owner", "operator_path": "offline_first_admin"},
        )
    ]


def test_existing_same_active_owner_is_idempotent_noop(monkeypatch):
    existing = AdminUser(
        id=7,
        email="owner@flashin.store",
        password_hash="original-hash",
        role="owner",
        active=True,
    )
    db = FakeDb([existing])
    monkeypatch.setattr(seed_admin, "log_admin_action", lambda *args, **kwargs: None)

    admin, created = seed_admin.seed_first_admin(
        db,
        email="owner@flashin.store",
        password=STRONG_PASSWORD,
    )

    assert admin is existing
    assert created is False
    assert existing.password_hash == "original-hash"
    assert len(db.admins) == 1
    assert db.flushes == 0


def test_existing_different_admin_closes_offline_creation(monkeypatch):
    existing = AdminUser(
        id=7,
        email="security@flashin.store",
        password_hash="original-hash",
        role="owner",
        active=True,
    )
    db = FakeDb([existing])
    monkeypatch.setattr(seed_admin, "log_admin_action", lambda *args, **kwargs: None)

    with pytest.raises(seed_admin.SeedAdminError, match="bootstrap is closed"):
        seed_admin.seed_first_admin(
            db,
            email="other-owner@flashin.store",
            password=STRONG_PASSWORD,
        )

    assert db.admins == [existing]
    assert db.flushes == 0


@pytest.mark.parametrize(
    ("role", "active"),
    [("manager", True), ("owner", False)],
)
def test_existing_bootstrap_identity_cannot_be_promoted_or_reactivated(
    monkeypatch, role, active
):
    existing = AdminUser(
        id=7,
        email="owner@flashin.store",
        password_hash="original-hash",
        role=role,
        active=active,
    )
    db = FakeDb([existing])
    monkeypatch.setattr(seed_admin, "log_admin_action", lambda *args, **kwargs: None)

    with pytest.raises(seed_admin.SeedAdminError, match="not an active owner"):
        seed_admin.seed_first_admin(
            db,
            email="owner@flashin.store",
            password=STRONG_PASSWORD,
        )

    assert existing.role == role
    assert existing.active is active
    assert existing.password_hash == "original-hash"


def test_seed_uses_same_strong_password_policy(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(seed_admin, "log_admin_action", lambda *args, **kwargs: None)

    with pytest.raises(seed_admin.SeedAdminError, match="three character classes"):
        seed_admin.seed_first_admin(
            db,
            email="owner@flashin.store",
            password="onlylowercasepassword",
        )

    assert db.admins == []


def test_production_password_prompt_requires_interactive_terminal(monkeypatch):
    monkeypatch.setattr(seed_admin.sys, "stdin", SimpleNamespace(isatty=lambda: False))

    with pytest.raises(seed_admin.SeedAdminError, match="interactive terminal"):
        seed_admin._prompt_production_password()


def test_production_password_prompt_confirms_hidden_input(monkeypatch):
    monkeypatch.setattr(seed_admin.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    values = iter([STRONG_PASSWORD, STRONG_PASSWORD])
    monkeypatch.setattr(seed_admin.getpass, "getpass", lambda prompt: next(values))

    assert seed_admin._prompt_production_password() == STRONG_PASSWORD


def test_seed_cli_exposes_no_password_argument():
    option_strings = {
        option
        for action in seed_admin.build_parser()._actions
        for option in action.option_strings
    }

    assert "--password" not in option_strings
    assert "--admin-password" not in option_strings
    assert seed_admin.ACK_FLAG in option_strings
