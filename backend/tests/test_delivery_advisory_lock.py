from backend.services import delivery_providers


class _Dialect:
    name = "postgresql"


class _Bind:
    dialect = _Dialect()


class _Db:
    def __init__(self):
        self.calls = []

    def get_bind(self):
        return _Bind()

    def execute(self, statement, params):
        self.calls.append((str(statement), params))


def test_delivery_lock_uses_postgresql_transaction_advisory_lock():
    db = _Db()

    delivery_providers._acquire_delivery_lock(db, 42)

    assert len(db.calls) == 1
    sql, params = db.calls[0]
    assert "pg_advisory_xact_lock" in sql
    assert isinstance(params["lock_key"], int)
    assert params["lock_key"] == delivery_providers._delivery_lock_key(42)
    assert params["lock_key"] != delivery_providers._delivery_lock_key(43)
