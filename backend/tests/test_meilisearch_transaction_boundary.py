import sys
from types import SimpleNamespace

import pytest

from backend.api import search as search_api
from backend.services import meili, meili_settings


class _Query:
    def __init__(self, session, rows):
        self.session = session
        self.rows = rows

    def options(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self.rows)


class _Session:
    def __init__(self, rows=None):
        self.active = False
        self.rollbacks = 0
        self.rows = list(rows or [])

    def in_transaction(self):
        return self.active

    def rollback(self):
        self.rollbacks += 1
        self.active = False

    def query(self, _entity):
        self.active = True
        return _Query(self, self.rows)


class _GuardedProduct:
    def __init__(self, session):
        self._session = session
        self.id = 7
        self.sku = "SKU-7"
        self.title = "Product 7"
        self.brand = "FLASHIN"
        self.description = "Detached search document"
        self.price = 1250.0
        self.currency = "RUB"
        self.category = "Clothing"
        self.gender = "unisex"
        self.active = True

    @property
    def images(self):
        if not self._session.in_transaction():
            raise AssertionError("ORM relationship accessed after DB transaction ended")
        return [SimpleNamespace(url="https://cdn.flashin.test/product-7.webp")]


def _open_permission_transaction(db, _admin, _permission):
    db.active = True


def test_admin_rebuild_snapshots_before_provider_and_closes_db_transaction(monkeypatch):
    db = _Session()
    db.rows = [_GuardedProduct(db)]

    monkeypatch.setattr(search_api, "require_permission", _open_permission_transaction)

    def rebuild_local_index(_db):
        assert _db is db
        assert db.in_transaction() is True
        # The real rebuild_search_index commits its durable local index writes.
        db.active = False
        return 1

    monkeypatch.setattr(search_api, "rebuild_search_index", rebuild_local_index)

    captured = {}

    def index_documents(documents):
        assert db.in_transaction() is False
        assert all(isinstance(document, dict) for document in documents)
        captured["documents"] = documents
        return len(documents)

    monkeypatch.setattr(search_api, "index_product_documents", index_documents)

    result = search_api.rebuild(admin=SimpleNamespace(id=1), db=db)

    assert result == {"ok": True, "indexed": 1, "meilisearch_indexed": 1}
    assert db.rollbacks == 1
    assert db.in_transaction() is False
    assert captured["documents"] == [
        {
            "id": 7,
            "sku": "SKU-7",
            "title": "Product 7",
            "brand": "FLASHIN",
            "description": "Detached search document",
            "price": 1250.0,
            "currency": "RUB",
            "category": "Clothing",
            "gender": "unisex",
            "active": True,
            "image_url": "https://cdn.flashin.test/product-7.webp",
        }
    ]


def test_admin_rebuild_provider_failure_leaves_no_local_transaction(monkeypatch):
    db = _Session()
    db.rows = [_GuardedProduct(db)]

    monkeypatch.setattr(search_api, "require_permission", _open_permission_transaction)

    def rebuild_local_index(_db):
        db.active = False
        return 1

    monkeypatch.setattr(search_api, "rebuild_search_index", rebuild_local_index)

    def fail_provider(_documents):
        assert db.in_transaction() is False
        raise TimeoutError("Meilisearch timeout")

    monkeypatch.setattr(search_api, "index_product_documents", fail_provider)

    with pytest.raises(TimeoutError, match="Meilisearch timeout"):
        search_api.rebuild(admin=SimpleNamespace(id=1), db=db)

    assert db.in_transaction() is False


def test_admin_configure_is_transaction_clean_at_every_provider_mutation(monkeypatch):
    db = _Session()
    monkeypatch.setattr(search_api, "require_permission", _open_permission_transaction)

    calls = []

    class _Index:
        def _record(self, name, value):
            assert db.in_transaction() is False
            calls.append((name, value))

        def update_searchable_attributes(self, value):
            self._record("searchable", value)

        def update_filterable_attributes(self, value):
            self._record("filterable", value)

        def update_sortable_attributes(self, value):
            self._record("sortable", value)

        def update_ranking_rules(self, value):
            self._record("ranking", value)

    class _Client:
        def index(self, name):
            assert db.in_transaction() is False
            assert name == "products"
            return _Index()

    monkeypatch.setattr(
        meili_settings,
        "get_settings",
        lambda: SimpleNamespace(meilisearch_products_index="products"),
    )
    monkeypatch.setattr(meili_settings, "_client", lambda: _Client())

    result = search_api.configure_meili(admin=SimpleNamespace(id=1), db=db)

    assert result == {"enabled": True, "index": "products"}
    assert db.rollbacks == 1
    assert db.in_transaction() is False
    assert calls == [
        ("searchable", ["title", "sku", "brand", "category", "description"]),
        ("filterable", ["brand", "category", "gender", "active", "price"]),
        ("sortable", ["price", "id"]),
        ("ranking", ["words", "typo", "proximity", "attribute", "sort", "exactness"]),
    ]


def test_public_meilisearch_query_runs_before_first_local_db_query(monkeypatch):
    row = SimpleNamespace(id=7)
    db = _Session([row])

    def provider_search(query):
        assert query == "flashin"
        assert db.in_transaction() is False
        return [7]

    monkeypatch.setattr(search_api, "search_products_meili", provider_search)

    result = search_api.search(q="flashin", db=db)

    assert result == [row]
    assert db.in_transaction() is True


def test_public_search_fallback_semantics_are_unchanged(monkeypatch):
    db = _Session()
    expected = [SimpleNamespace(id=9)]
    monkeypatch.setattr(search_api, "search_products_meili", lambda _query: [])

    def local_search(_db, query):
        assert _db is db
        assert query == "fallback"
        assert db.in_transaction() is False
        return expected

    monkeypatch.setattr(search_api, "search_products", local_search)

    assert search_api.search(q="fallback", db=db) is expected


def test_meilisearch_client_uses_bounded_timeout(monkeypatch):
    captured = {}

    class _Client:
        def __init__(self, url, api_key, timeout=None):
            captured["url"] = url
            captured["api_key"] = api_key
            captured["timeout"] = timeout

    monkeypatch.setattr(
        meili,
        "get_settings",
        lambda: SimpleNamespace(
            meilisearch_enabled=True,
            meilisearch_url="http://meilisearch:7700",
            meilisearch_master_key="secret",
            meilisearch_timeout_seconds=7,
        ),
    )
    monkeypatch.setitem(sys.modules, "meilisearch", SimpleNamespace(Client=_Client))

    client = meili._client()

    assert isinstance(client, _Client)
    assert captured == {
        "url": "http://meilisearch:7700",
        "api_key": "secret",
        "timeout": 7,
    }


def test_index_transport_copies_primitive_documents(monkeypatch):
    sent = {}

    class _Index:
        def add_documents(self, documents, primary_key):
            sent["documents"] = documents
            sent["primary_key"] = primary_key

    class _Client:
        def index(self, name):
            assert name == "products"
            return _Index()

    monkeypatch.setattr(meili, "_client", lambda: _Client())
    monkeypatch.setattr(
        meili,
        "get_settings",
        lambda: SimpleNamespace(meilisearch_products_index="products"),
    )

    source = {"id": 1, "title": "Original"}
    assert meili.index_product_documents([source]) == 1
    source["title"] = "Mutated later"

    assert sent == {
        "documents": [{"id": 1, "title": "Original"}],
        "primary_key": "id",
    }
