from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import OrderItem
from ..provider_models import ProviderCommand
from ..reverse_logistics_models import ReturnLogisticsCase, ReturnLogisticsItem
from .moysklad_outbound import (
    MoySkladDependencyPending,
    MoySkladReviewRequired,
    _allocate_net_line_totals,
    _base_document,
    _load_order_items,
    _request_json,
    _require_clean_export_session,
    _require_export_configuration,
    _resolve_assortment_meta,
    _snapshot_order,
    _sync_id,
)
from .provider_commands import enqueue_provider_command


@dataclass(frozen=True)
class _PhysicalReturnLine:
    moysklad_id: str
    quantity: int
    net_total_cents: int


@dataclass(frozen=True)
class _PhysicalReturnSnapshot:
    case_id: int
    return_request_id: int
    order_snapshot: object
    demand_external_id: str
    lines: tuple[_PhysicalReturnLine, ...]


def _prorated_cents(full_line_cents: int, returned_qty: int, ordered_qty: int) -> int:
    if ordered_qty <= 0 or returned_qty <= 0 or returned_qty > ordered_qty:
        raise MoySkladReviewRequired("Physical return quantity is invalid")
    return int(
        (Decimal(full_line_cents) * Decimal(returned_qty) / Decimal(ordered_qty)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


def _prepare_physical_return_snapshot(db: Session, case_id: int) -> _PhysicalReturnSnapshot:
    _require_clean_export_session(db)
    try:
        case = db.query(ReturnLogisticsCase).filter(ReturnLogisticsCase.id == case_id).first()
        if case is None:
            raise MoySkladReviewRequired(f"Physical return case {case_id} does not exist")
        if case.status != "inspected":
            raise MoySkladReviewRequired("MoySklad sales return requires completed physical inspection")

        order, loaded_items = _load_order_items(db, case.order_id)
        order_snapshot = _snapshot_order(order, loaded_items)
        line_totals = _allocate_net_line_totals(order, loaded_items)
        order_lines: dict[int, tuple[OrderItem, object, object, int]] = {
            int(item.id): (item, variant, product, int(line_total))
            for (item, variant, product), line_total in zip(loaded_items, line_totals, strict=True)
        }

        physical_items = (
            db.query(ReturnLogisticsItem)
            .filter(ReturnLogisticsItem.case_id == case.id, ReturnLogisticsItem.inspected_qty > 0)
            .order_by(ReturnLogisticsItem.id.asc())
            .all()
        )
        if not physical_items:
            raise MoySkladReviewRequired("Physical return has no inspected items")

        lines: list[_PhysicalReturnLine] = []
        for physical in physical_items:
            if int(physical.inspected_qty) != int(physical.authorized_qty):
                raise MoySkladReviewRequired("Physical return inspection is incomplete")
            loaded = order_lines.get(int(physical.order_item_id))
            if loaded is None:
                raise MoySkladReviewRequired("Physical return item is not part of the original order")
            order_item, variant, product, full_line_cents = loaded
            if int(order_item.variant_id) != int(physical.variant_id):
                raise MoySkladReviewRequired("Physical return variant differs from original order")
            moysklad_id = str(variant.moysklad_id or product.moysklad_id or "").strip()
            if not moysklad_id:
                raise MoySkladReviewRequired("Physical return item has no MoySklad mapping")
            lines.append(
                _PhysicalReturnLine(
                    moysklad_id=moysklad_id,
                    quantity=int(physical.inspected_qty),
                    net_total_cents=_prorated_cents(
                        full_line_cents,
                        int(physical.inspected_qty),
                        int(order_item.quantity),
                    ),
                )
            )

        demand_command = (
            db.query(ProviderCommand)
            .filter(
                ProviderCommand.provider == "moysklad",
                ProviderCommand.idempotency_key == f"order:{order.id}:demand:v1",
                ProviderCommand.status == "sent",
            )
            .first()
        )
        if demand_command is None or not str(demand_command.external_id or "").strip():
            raise MoySkladDependencyPending("MoySklad demand dependency is not completed yet")

        return _PhysicalReturnSnapshot(
            case_id=int(case.id),
            return_request_id=int(case.return_request_id),
            order_snapshot=order_snapshot,
            demand_external_id=str(demand_command.external_id).strip(),
            lines=tuple(lines),
        )
    finally:
        if db.in_transaction():
            db.rollback()


async def _physical_return_positions(snapshot: _PhysicalReturnSnapshot) -> list[dict]:
    positions: list[dict] = []
    for line in snapshot.lines:
        assortment = await _resolve_assortment_meta(line.moysklad_id)
        base_price, remainder = divmod(line.net_total_cents, line.quantity)
        base_quantity = line.quantity - remainder
        if base_quantity:
            positions.append({
                "assortment": assortment,
                "quantity": base_quantity,
                "price": base_price,
                "discount": 0,
                "vat": 0,
            })
        if remainder:
            positions.append({
                "assortment": assortment,
                "quantity": remainder,
                "price": base_price + 1,
                "discount": 0,
                "vat": 0,
            })
    return positions


async def export_physical_sales_return(db: Session, case_id: int) -> str:
    _require_export_configuration()
    snapshot = _prepare_physical_return_snapshot(db, case_id)
    positions = await _physical_return_positions(snapshot)
    sync_id = _sync_id("physical-salesreturn", snapshot.case_id)
    payload = _base_document(snapshot.order_snapshot, positions, sync_id)
    payload["externalCode"] = f"FLASHIN-PHYSICAL-RETURN-{snapshot.case_id}"
    payload["demand"] = {
        "meta": {
            "href": f"{get_settings().moysklad_base_url.rstrip('/')}/entity/demand/{snapshot.demand_external_id}",
            "type": "demand",
            "mediaType": "application/json",
        }
    }
    payload["description"] = (
        f"FLASHIN physical return case #{snapshot.case_id}; "
        f"return_request=#{snapshot.return_request_id}; inspected quantities only"
    )[:4096]
    result = await _request_json("POST", "entity/salesreturn", json_body=payload)
    external_id = str(result.get("id") or "").strip()
    if not external_id:
        raise MoySkladReviewRequired("MoySklad physical sales return returned no id")
    return external_id


def enqueue_moysklad_physical_sales_return(db: Session, case_id: int):
    if not get_settings().moysklad_order_export_enabled:
        return None
    return enqueue_provider_command(
        db,
        provider="moysklad",
        command_type="moysklad.physical_sales_return.create",
        idempotency_key=f"physical-return:{int(case_id)}:sales_return:v1",
        aggregate_type="return_logistics_case",
        aggregate_id=case_id,
        payload={"case_id": int(case_id)},
    )
