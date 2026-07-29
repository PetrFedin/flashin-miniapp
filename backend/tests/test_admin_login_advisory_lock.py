from types import SimpleNamespace

from backend.services.admin_login_lockout import acquire_admin_login_locks


class FakePostgresSession:
    def __init__(self):
        self.calls = []

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    def execute(self, statement, parameters):
        self.calls.append((str(statement), parameters))


class FakeSqliteSession(FakePostgresSession):
    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))


def test_postgres_acquires_email_and_ip_transaction_locks():
    db = FakePostgresSession()

    acquire_admin_login_locks(db, " Admin@Test.Local ", "203.0.113.10")

    assert len(db.calls) == 2
    assert all("pg_advisory_xact_lock" in sql for sql, _ in db.calls)
    keys = [parameters["lock_key"] for _, parameters in db.calls]
    assert len(set(keys)) == 2
    assert all(isinstance(value, int) for value in keys)


def test_non_postgres_databases_skip_advisory_lock_primitive():
    db = FakeSqliteSession()

    acquire_admin_login_locks(db, "admin@test.local", "203.0.113.10")

    assert db.calls == []
