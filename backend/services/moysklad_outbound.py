from __future__ import annotations

import base64
import json
import uuid
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

import httpx
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Order, OrderItem, Product, ProductVariant, ReturnRequest
from .provider_commands import enqueue_provider_command

_MONEY = Decimal("0.01")
_SYNC_NAMESPACE = uuid.UUID("d5288fc4-9e28-4de8-8a0e-cbb8c1cc1a9f")


class MoySkladReviewRequired(RuntimeError):
    """Permanent/configuration/data issue that must not be retried blindly."""


def _money_cents(value: object, field: str) -> int:
    try:
        amount = Decimal(str(value)).quantize(_MONEY, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MoySkladReviewRequired(f"Invalid {field}") from exc
    if not amount.is_finite() or amount < 0:
        raise MoySkladReviewRequired(f"Invalid {field}")
    return int((amount * 100).to_integral_value(rounding=ROUND_HALF_UP))


def _headers() -> dict[str, str]:
    settings = get_settings()
    headers = {
        "Accept": "application/json;charset=utf-8",
        "Content-Type": "application/json",
    }
    if settings.moysklad_token.strip():
        headers["Authorization"] = f"Bearer {settings.moysklad_token.strip()}"
    elif settings.moysklad_login.strip() and settings.moysklad_password:
        encoded = base64.b64encode(
            f"{settings.moysklad_login}:{settings.moysklad_password}".encode("utf-8")
        ).decode("ascii")
        headers["Authorization"] = f"Basic {encoded}"
    else:
        raise MoySkladReviewRequired("MoySklad credentials are missing")
    return headers


def _sync_id(kind: str, local_id: int) -> str:
    return str(uuid.uuid5(_SYNC_NAMESPACE, f"flashin:{kind}:{int(local_id)}"))


def _entity_meta(entity_type: str, entity_id: str) -> dict[str, Any]:
    settings = get_settings()
    clean_id = str(entity_id or "").strip()
    if not clean_id:
        raise MoySkladReviewRequired(f"MoySklad {entity_type} id is missing")
    return {
        "meta": {
            "href": f"{settings.moysklad_base_url.rstrip('/')}/entity/{entity_type}/{clean_id}",
            "type": entity_type,
            "mediaType": "application/json",
        }
    }


def _require_export_configuration() -> None:
    settings = get_settings()
    if not settings.moysklad_order_export_enabled:
        raise MoySkladReviewRequired("MoySklad order export is disabled")
    _headers()
    for field, value in (
        ("MOYSKLAD_ORGANIZATION_ID", settings.moysklad_organization_id),
        ("MOYSKLAD_AGENT_ID", settings.moysklad_agent_id),
        ("MOYSKLAD_STORE_ID", settings.moysklad_store_id),
    ):
        if not str(value or "").strip():
            raise MoySkladReviewRequired(f"{field} is required for outbound documents")


async def _request_json(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    url = f"{settings.moysklad_base_url.rstrip('/')}/{path.lstrip('/')}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(
                method,
                url,
                headers=_headers(),
                json=json_body,
                params=params,
            )
    except httpx.HTTPError:
        raise

    if 400 <= response.status_code < 500 and response.status_code != 429:
        detail = response.text[:1000].strip()
        raise MoySkladReviewRequired(
            f"MoySklad rejected {path}: HTTP {response.status_code}: {detail}"
        )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise MoySkladReviewRequired("MoySklad response must be a JSON object")
    return payload


async def _resolve_assortment_meta(moysklad_id: str) -> dict[str, Any]:
    clean_id = str(moysklad_id or "").strip()
    if not clean_id:
        raise MoySkladReviewRequired("Order item has no MoySklad mapping")
    payload = await _request_json(
        "GET",
        "entity/assortment",
        params={"filter": f"id={clean_id}", "limit": 2},
    )
    rows = payload.get("rows")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise MoySkladReviewRequired(
            f"MoySklad assortment mapping {clean_id} did not resolve uniquely"
        )
    meta = rows[0].get("meta")
    if not isinstance(meta, dict) or not str(meta.get("href") or "").strip():
        raise MoySkladReviewRequired(
            f"MoySklad assortment mapping {clean_id} returned no meta href"
        )
    return {"meta": meta}


def _load_order_items(db: Session, order_id: int) -> tuple[Order, list[tuple[OrderItem, ProductVariant, Product]]]:
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise MoySkladReviewRequired(f"Order {order_id} does not exist")
    raw_items = (
        db.query(OrderItem)
        .filter(OrderItem.order_id == order.id)
        .order_by(OrderItem.id.asc())
        .all()
    )
    if not raw_items:
        raise MoySkladReviewRequired(f"Order {order_id} has no items")

    items: list[tuple[OrderItem, ProductVariant, Product]] = []
    for item in raw_items:
        variant = db.query(ProductVariant).filter(ProductVariant.id == item.variant_id).first()
        product = db.query(Product).filter(Product.id == item.product_id).first()
        if not variant or not product:
            raise MoySkladReviewRequired(f"Order item {item.id} has a broken catalog link")
        if variant.product_id != product.id:
            raise MoySkladReviewRequired(f"Order item {item.id} variant/product mismatch")
        if not str(variant.moysklad_id or product.moysklad_id or "").strip():
            raise MoySkladReviewRequired(f"Order item {item.id} is not mapped to MoySklad")
        if isinstance(item.quantity, bool) or not isinstance(item.quantity, int) or item.quantity <= 0:
            raise MoySkladReviewRequired(f"Order item {item.id} has invalid quantity")
        items.append((item, variant, product))
    return order, items


def _allocate_net_line_totals(order: Order, items: list[tuple[OrderItem, ProductVariant, Product]]) -> list[int]:
    gross_lines = [_money_cents(item.price, "order item price") * item.quantity for item, _, _ in items]
    gross_total = sum(gross_lines)
    if gross_total <= 0:
        raise MoySkladReviewRequired("Order merchandise total must be positive")

    discount = _money_cents(order.discount_amount, "order discount")
    loyalty = _money_cents(order.loyalty_discount_amount, "loyalty discount")
    delivery = _money_cents(order.delivery_price, "delivery price")
    order_total = _money_cents(order.total_amount, "order total")
    merchandise_target = gross_total - discount - loyalty
    if merchandise_target < 0 or merchandise_target + delivery != order_total:
        raise MoySkladReviewRequired(
            "Order monetary breakdown does not reconcile before MoySklad export"
        )

    remaining_target = merchandise_target
    remaining_gross = gross_total
    line_totals: list[int] = []
    for index, gross in enumerate(gross_lines):
        if index == len(gross_lines) - 1:
            allocated = remaining_target
        else:
            allocated = (gross * remaining_target) // remaining_gross
        if allocated < 0 or allocated > gross:
            raise MoySkladReviewRequired("Order discount allocation is invalid")
        line_totals.append(allocated)
        remaining_target -= allocated
        remaining_gross -= gross
    if sum(line_totals) != merchandise_target:
        raise MoySkladReviewRequired("Order discount allocation did not reconcile")
    return line_totals


async def _document_positions(
    db: Session,
    order_id: int,
) -> tuple[Order, list[dict[str, Any]]]:
    order, items = _load_order_items(db, order_id)
    line_totals = _allocate_net_line_totals(order, items)
    positions: list[dict[str, Any]] = []

    for (item, variant, product), line_total in zip(items, line_totals, strict=True):
        assortment = await _resolve_assortment_meta(variant.moysklad_id or product.moysklad_id)
        base_price, remainder = divmod(line_total, item.quantity)
        base_quantity = item.quantity - remainder
        if base_quantity:
            positions.append(
                {
                    "assortment": assortment,
                    "quantity": base_quantity,
                    "price": base_price,
                    "discount": 0,
                    "vat": 0,
                }
            )
        if remainder:
            positions.append(
                {
                    "assortment": assortment,
                    "quantity": remainder,
                    "price": base_price + 1,
                    "discount": 0,
                    "vat": 0,
                }
            )

    delivery_cents = _money_cents(order.delivery_price, "delivery price")
    if delivery_cents:
        settings = get_settings()
        if not settings.moysklad_delivery_service_id.strip():
            raise MoySkladReviewRequired(
                "MOYSKLAD_DELIVERY_SERVICE_ID is required for paid delivery"
            )
        delivery_meta = await _resolve_assortment_meta(settings.moysklad_delivery_service_id)
        positions.append(
            {
                "assortment": delivery_meta,
                "quantity": 1,
                "price": delivery_cents,
                "discount": 0,
                "vat": 0,
            }
        )

    rendered_total = sum(int(position["price"]) * int(position["quantity"]) for position in positions)
    expected_total = _money_cents(order.total_amount, "order total")
    if rendered_total != expected_total:
        raise MoySkladReviewRequired(
            f"MoySklad position total {rendered_total} does not match order total {expected_total}"
        )
    return order, positions


def _base_document(order: Order, positions: list[dict[str, Any]], sync_id: str) -> dict[str, Any]:
    settings = get_settings()
    return {
        "syncId": sync_id,
        "organization": _entity_meta("organization", settings.moysklad_organization_id),
        "agent": _entity_meta("counterparty", settings.moysklad_agent_id),
        "store": _entity_meta("store", settings.moysklad_store_id),
        "description": (
            f"FLASHIN order #{order.id}; local_total={Decimal(str(order.total_amount)).quantize(_MONEY)} "
            f"{order.currency}; payment_status={order.payment_status}; delivery={order.delivery_type}"
        )[:4096],
        "positions": positions,
        "vatEnabled": False,
        "vatIncluded": True,
    }


async def export_customer_order(db: Session, order_id: int) -> str:
    _require_export_configuration()
    order, positions = await _document_positions(db, order_id)
    if order.payment_status not in {"paid", "partially_refunded", "refunded"}:
        raise MoySkladReviewRequired("Only a paid order can be exported to MoySklad")
    sync_id = _sync_id("customerorder", order.id)
    payload = _base_document(order, positions, sync_id)
    payload["externalCode"] = f"FLASHIN-ORDER-{order.id}"
    result = await _request_json("POST", "entity/customerorder", json_body=payload)
    external_id = str(result.get("id") or "").strip()
    if not external_id:
        raise MoySkladReviewRequired("MoySklad customer order returned no id")
    return external_id


async def export_demand(db: Session, order_id: int) -> str:
    _require_export_configuration()
    order, positions = await _document_positions(db, order_id)
    if order.delivery_status not in {"shipped", "delivered"}:
        raise MoySkladReviewRequired("Only a shipped order can create a MoySklad demand")
    sync_id = _sync_id("demand", order.id)
    payload = _base_document(order, positions, sync_id)
    payload["externalCode"] = f"FLASHIN-DEMAND-{order.id}"
    result = await _request_json("POST", "entity/demand", json_body=payload)
    external_id = str(result.get("id") or "").strip()
    if not external_id:
        raise MoySkladReviewRequired("MoySklad demand returned no id")
    return external_id


async def export_sales_return(db: Session, order_id: int, return_id: int) -> str:
    _require_export_configuration()
    order, positions = await _document_positions(db, order_id)
    ret = db.query(ReturnRequest).filter(ReturnRequest.id == return_id, ReturnRequest.order_id == order.id).first()
    if not ret:
        raise MoySkladReviewRequired(f"Return request {return_id} does not exist")
    if ret.status != "approved" or order.payment_status != "refunded":
        raise MoySkladReviewRequired(
            "MoySklad stock return requires a completed full refund with unambiguous item composition"
        )

    demand_command_key = f"order:{order.id}:demand:v1"
    from ..provider_models import ProviderCommand

    demand_command = (
        db.query(ProviderCommand)
        .filter(
            ProviderCommand.provider == "moysklad",
            ProviderCommand.idempotency_key == demand_command_key,
            ProviderCommand.status == "sent",
        )
        .first()
    )
    if not demand_command or not demand_command.external_id:
        raise MoySkladReviewRequired(
            "A completed MoySklad demand is required before creating a sales return"
        )

    sync_id = _sync_id("salesreturn", ret.id)
    payload = _base_document(order, positions, sync_id)
    payload["externalCode"] = f"FLASHIN-RETURN-{ret.id}"
    payload["demand"] = _entity_meta("demand", demand_command.external_id)
    payload["description"] = (
        f"FLASHIN full refund return #{ret.id} for order #{order.id}; "
        f"provider_refund_id={ret.provider_refund_id}"
    )[:4096]
    result = await _request_json("POST", "entity/salesreturn", json_body=payload)
    external_id = str(result.get("id") or "").strip()
    if not external_id:
        raise MoySkladReviewRequired("MoySklad sales return returned no id")
    return external_id


def enqueue_moysklad_customer_order(db: Session, order_id: int):
    if not get_settings().moysklad_order_export_enabled:
        return None
    return enqueue_provider_command(
        db,
        provider="moysklad",
        command_type="moysklad.customer_order.create",
        idempotency_key=f"order:{int(order_id)}:customer_order:v1",
        aggregate_type="order",
        aggregate_id=order_id,
        payload={"order_id": int(order_id)},
    )


def enqueue_moysklad_demand(db: Session, order_id: int):
    if not get_settings().moysklad_order_export_enabled:
        return None
    return enqueue_provider_command(
        db,
        provider="moysklad",
        command_type="moysklad.demand.create",
        idempotency_key=f"order:{int(order_id)}:demand:v1",
        aggregate_type="order",
        aggregate_id=order_id,
        payload={"order_id": int(order_id)},
    )


def enqueue_moysklad_sales_return(db: Session, order_id: int, return_id: int):
    if not get_settings().moysklad_order_export_enabled:
        return None
    return enqueue_provider_command(
        db,
        provider="moysklad",
        command_type="moysklad.sales_return.create",
        idempotency_key=f"return:{int(return_id)}:sales_return:v1",
        aggregate_type="return",
        aggregate_id=return_id,
        payload={"order_id": int(order_id), "return_id": int(return_id)},
    )
