import asyncio
from types import SimpleNamespace

import pytest

from backend.jobs import provider_command_jobs
from backend.models import ReturnRequest
from backend.provider_models import ProviderCommand
from backend.services import moysklad_outbound


class _SnapshotSession:
    def __init__(self, *, return_request=None, demand_command=None):
        self.active = False
        self.rollbacks = 0
        self.return_request = return_request
        self.demand_command = demand_command

    def in_transaction(self):
        return self.active

    def rollback(self):
        self.rollbacks += 1
        self.active = False

    def query(self, entity):
        self.active = True
        if entity is ReturnRequest:
            return _Query(self.return_request)
        if entity is ProviderCommand:
            return _Query(self.demand_command)
        raise AssertionError(f"unexpected entity: {entity!r}")


class _Query:
    def __init__(self, value):
        self.value = value

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.value


def _order(*, payment_status="paid", delivery_status="pending"):
    return SimpleNamespace(
        id=42,
        total_amount="1250.00",
        discount_amount="50.00",
        loyalty_discount_amount="0.00",
        delivery_price="0.00",
        currency="RUB",
        payment_status=payment_status,
        delivery_status=delivery_status,
        delivery_type="pickup",
    )


def _items():
    item = SimpleNamespace(
        id=7,
        price="1300.00",
        quantity=1,
        variant_id=11,
        product_id=9,
    )
    variant = SimpleNamespace(id=11, product_id=9, moysklad_id="variant-ms-id")
    product = SimpleNamespace(id=9, moysklad_id="product-ms-id")
    return [(item, variant, product)]


def _install_order_loader(monkeypatch, db, order):
    def load_order_items(_db, order_id):
        assert _db is db
        assert order_id == 42
        db.active = True
        return order, _items()

    monkeypatch.setattr(moysklad_outbound, "_load_order_items", load_order_items)


def _install_provider_probe(monkeypatch, db, expected_post_path):
    calls = []

    async def request_json(method, path, *, json_body=None, params=None):
        assert db.in_transaction() is False
        calls.append((method, path))
        if method == "GET":
            assert path == "entity/assortment"
            return {
                "rows": [
                    {
                        "meta": {
                            "href": "https://api.moysklad.test/entity/variant/variant-ms-id",
                            "type": "variant",
                            "mediaType": "application/json",
                        }
                    }
                ]
            }
        assert method == "POST"
        assert path == expected_post_path
        assert json_body is not None
        return {"id": "provider-document-id"}

    monkeypatch.setattr(moysklad_outbound, "_request_json", request_json)
    return calls


def test_customer_order_has_no_open_db_transaction_during_provider_http(monkeypatch):
    db = _SnapshotSession()
    _install_order_loader(monkeypatch, db, _order(payment_status="paid"))
    calls = _install_provider_probe(monkeypatch, db, "entity/customerorder")
    monkeypatch.setattr(moysklad_outbound, "_require_export_configuration", lambda: None)

    external_id = asyncio.run(moysklad_outbound.export_customer_order(db, 42))

    assert external_id == "provider-document-id"
    assert calls == [("GET", "entity/assortment"), ("POST", "entity/customerorder")]
    assert db.rollbacks == 1
    assert db.in_transaction() is False


def test_demand_has_no_open_db_transaction_during_provider_http(monkeypatch):
    db = _SnapshotSession()
    _install_order_loader(
        monkeypatch,
        db,
        _order(payment_status="paid", delivery_status="shipped"),
    )
    calls = _install_provider_probe(monkeypatch, db, "entity/demand")
    monkeypatch.setattr(moysklad_outbound, "_require_export_configuration", lambda: None)

    external_id = asyncio.run(moysklad_outbound.export_demand(db, 42))

    assert external_id == "provider-document-id"
    assert calls == [("GET", "entity/assortment"), ("POST", "entity/demand")]
    assert db.rollbacks == 1
    assert db.in_transaction() is False


def test_sales_return_has_no_open_db_transaction_during_provider_http(monkeypatch):
    ret = SimpleNamespace(
        id=17,
        order_id=42,
        status="approved",
        provider_refund_id="refund-1",
    )
    demand = SimpleNamespace(external_id="demand-provider-id")
    db = _SnapshotSession(return_request=ret, demand_command=demand)
    _install_order_loader(monkeypatch, db, _order(payment_status="refunded"))
    calls = _install_provider_probe(monkeypatch, db, "entity/salesreturn")
    monkeypatch.setattr(moysklad_outbound, "_require_export_configuration", lambda: None)

    external_id = asyncio.run(moysklad_outbound.export_sales_return(db, 42, 17))

    assert external_id == "provider-document-id"
    assert calls == [("GET", "entity/assortment"), ("POST", "entity/salesreturn")]
    assert db.rollbacks == 1
    assert db.in_transaction() is False


def test_missing_demand_dependency_is_retryable_and_snapshot_transaction_is_closed(monkeypatch):
    ret = SimpleNamespace(
        id=17,
        order_id=42,
        status="approved",
        provider_refund_id="refund-1",
    )
    db = _SnapshotSession(return_request=ret, demand_command=None)
    _install_order_loader(monkeypatch, db, _order(payment_status="refunded"))
    monkeypatch.setattr(moysklad_outbound, "_require_export_configuration", lambda: None)

    with pytest.raises(moysklad_outbound.MoySkladDependencyPending):
        asyncio.run(moysklad_outbound.export_sales_return(db, 42, 17))

    assert db.rollbacks == 1
    assert db.in_transaction() is False


def test_export_rejects_preexisting_transaction_without_ending_callers_transaction(monkeypatch):
    db = _SnapshotSession()
    db.active = True
    monkeypatch.setattr(moysklad_outbound, "_require_export_configuration", lambda: None)

    with pytest.raises(
        moysklad_outbound.MoySkladReviewRequired,
        match="requires a clean database session",
    ):
        asyncio.run(moysklad_outbound.export_customer_order(db, 42))

    assert db.rollbacks == 0
    assert db.in_transaction() is True


def test_sales_return_worker_handler_does_not_open_dependency_transaction(monkeypatch):
    class _NoQuerySession:
        def query(self, *_args, **_kwargs):
            raise AssertionError("worker handler must not query before exporter snapshot")

    async def export_sales_return(db, order_id, return_id):
        assert isinstance(db, _NoQuerySession)
        assert order_id == 42
        assert return_id == 17
        return "sales-return-id"

    monkeypatch.setattr(provider_command_jobs, "export_sales_return", export_sales_return)

    result = asyncio.run(
        provider_command_jobs._sales_return(
            _NoQuerySession(),
            {"order_id": 42, "return_id": 17},
        )
    )

    assert result == "sales-return-id"
