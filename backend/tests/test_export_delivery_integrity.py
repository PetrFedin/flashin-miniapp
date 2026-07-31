import csv
import inspect
import io
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.api import import_export as export_api
from backend.database import Base
from backend.models import Product
from backend.services import import_export


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _decode_csv(chunks: list[bytes]) -> list[list[str]]:
    content = b"".join(chunks).decode("utf-8-sig")
    return list(csv.reader(io.StringIO(content)))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("=HYPERLINK(\"https://example.test\")", "'=HYPERLINK(\"https://example.test\")"),
        (" +SUM(1,2)", "' +SUM(1,2)"),
        ("\t@command", "'\t@command"),
        ("-danger", "'-danger"),
        ("ordinary text", "ordinary text"),
        (None, ""),
    ],
)
def test_csv_text_neutralizes_spreadsheet_formula_injection(value, expected):
    assert import_export._safe_csv_text(value) == expected


def test_product_export_streams_stable_values_and_utf8_bom():
    db = _session()
    db.add(
        Product(
            sku="SKU-1",
            title="=1+1",
            slug="sku-1",
            brand="FLASHIN",
            description="",
            price=Decimal("1000.005"),
            old_price=None,
            currency="rub",
            category="+Command",
            gender="unisex",
            active=True,
        )
    )
    db.commit()

    chunks = list(import_export.stream_products_csv(db))
    rows = _decode_csv(chunks)

    assert chunks[0].startswith(import_export.CSV_UTF8_BOM)
    assert rows[0] == ["id", "sku", "title", "price", "currency", "category", "active"]
    assert rows[1][1:] == ["SKU-1", "'=1+1", "1000.01", "RUB", "'+Command", "true"]


def test_stream_is_chunked_in_bounded_row_batches():
    rows = ((index, f"row-{index}") for index in range(import_export.CSV_BATCH_SIZE + 1))

    chunks = list(import_export._stream_csv(("id", "value"), rows))

    assert len(chunks) == 3
    decoded = _decode_csv(chunks)
    assert len(decoded) == import_export.CSV_BATCH_SIZE + 2
    assert decoded[-1] == [str(import_export.CSV_BATCH_SIZE), f"row-{import_export.CSV_BATCH_SIZE}"]


def test_money_and_datetime_export_are_deterministic():
    assert import_export._money("10.005") == "10.01"
    assert import_export._money(0) == "0.00"
    assert import_export._iso_utc(datetime(2026, 7, 31, 1, 2, 3)) == "2026-07-31T01:02:03Z"
    assert import_export._iso_utc(
        datetime(2026, 7, 31, 3, 2, 3, tzinfo=UTC) + timedelta(hours=0)
    ) == "2026-07-31T03:02:03Z"
    with pytest.raises(ValueError):
        import_export._money("NaN")


def test_export_filename_is_sanitized_and_microsecond_specific():
    first = export_filename_at = import_export.export_filename(
        "Products ../../",
        now=datetime(2026, 7, 31, 1, 2, 3, 1, tzinfo=UTC),
    )
    second = import_export.export_filename(
        "Products ../../",
        now=datetime(2026, 7, 31, 1, 2, 3, 2, tzinfo=UTC),
    )

    assert first == "products-20260731T010203000001Z.csv"
    assert second == "products-20260731T010203000002Z.csv"
    assert first != second
    assert "/" not in export_filename_at


def test_export_routes_require_admin_authentication():
    product_route = next(
        route
        for route in export_api.router.routes
        if route.path == "/import-export/admin/export/products"
    )
    order_route = next(
        route
        for route in export_api.router.routes
        if route.path == "/import-export/admin/export/orders"
    )

    for route in (product_route, order_route):
        dependency_names = {
            dependency.call.__name__
            for dependency in route.dependant.dependencies
            if dependency.call is not None
        }
        assert "get_current_admin" in dependency_names
        assert "get_db" in dependency_names


def test_download_response_is_attachment_and_never_cacheable():
    response = export_api._download_response(iter([b"id\r\n1\r\n"]), "products.csv")

    assert response.headers["content-disposition"] == 'attachment; filename="products.csv"'
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.media_type == import_export.CSV_MEDIA_TYPE


def test_export_service_does_not_write_shared_files_or_return_server_paths():
    source = inspect.getsource(import_export)

    assert "Path(" not in source
    assert ".open(" not in source
    assert "products_export.csv" not in source
    assert "orders_export.csv" not in source
