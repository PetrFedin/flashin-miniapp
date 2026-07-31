import csv
import io
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable, Iterator, Sequence

from sqlalchemy.orm import Session

from ..models import Order, Product

CSV_MEDIA_TYPE = "text/csv; charset=utf-8"
CSV_BATCH_SIZE = 500
CSV_UTF8_BOM = b"\xef\xbb\xbf"
_DANGEROUS_FORMULA_PREFIXES = ("=", "+", "-", "@")


def _safe_csv_text(value: object) -> str:
    """Neutralize spreadsheet formulas while preserving the exported text."""

    text = "" if value is None else str(value)
    probe = text.lstrip(" \t\r\n")
    if text.startswith(("\t", "\r")) or probe.startswith(_DANGEROUS_FORMULA_PREFIXES):
        return "'" + text
    return text


def _money(value: object) -> str:
    try:
        amount = Decimal(str(value or 0)).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Export contains an invalid monetary value") from exc
    if not amount.is_finite():
        raise ValueError("Export contains a non-finite monetary value")
    return format(amount, ".2f")


def _iso_utc(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    else:
        value = value.astimezone(UTC)
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _stream_csv(
    headers: Sequence[str],
    rows: Iterable[Sequence[object]],
) -> Iterator[bytes]:
    """Write bounded CSV chunks instead of building or persisting one large file."""

    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(headers)
    yield CSV_UTF8_BOM + buffer.getvalue().encode("utf-8")
    buffer.seek(0)
    buffer.truncate(0)

    buffered_rows = 0
    for row in rows:
        writer.writerow([_safe_csv_text(value) for value in row])
        buffered_rows += 1
        if buffered_rows >= CSV_BATCH_SIZE:
            yield buffer.getvalue().encode("utf-8")
            buffer.seek(0)
            buffer.truncate(0)
            buffered_rows = 0

    if buffer.tell():
        yield buffer.getvalue().encode("utf-8")


def product_export_rows(db: Session) -> Iterator[Sequence[object]]:
    query = db.query(Product).order_by(Product.id.asc()).yield_per(CSV_BATCH_SIZE)
    for product in query:
        yield (
            product.id,
            product.sku,
            product.title,
            _money(product.price),
            product.currency,
            product.category,
            "true" if product.active else "false",
        )


def order_export_rows(db: Session) -> Iterator[Sequence[object]]:
    query = db.query(Order).order_by(Order.id.asc()).yield_per(CSV_BATCH_SIZE)
    for order in query:
        yield (
            order.id,
            order.status,
            order.payment_status,
            _money(order.total_amount),
            order.currency,
            _iso_utc(order.created_at),
        )


def stream_products_csv(db: Session) -> Iterator[bytes]:
    return _stream_csv(
        ("id", "sku", "title", "price", "currency", "category", "active"),
        product_export_rows(db),
    )


def stream_orders_csv(db: Session) -> Iterator[bytes]:
    return _stream_csv(
        ("id", "status", "payment_status", "total_amount", "currency", "created_at"),
        order_export_rows(db),
    )


def export_filename(kind: str, *, now: datetime | None = None) -> str:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    else:
        current = current.astimezone(UTC)
    safe_kind = "".join(
        character for character in kind.lower() if character.isalnum() or character == "-"
    )
    if not safe_kind:
        raise ValueError("Export filename kind is invalid")
    return f"{safe_kind}-{current.strftime('%Y%m%dT%H%M%S%fZ')}.csv"
