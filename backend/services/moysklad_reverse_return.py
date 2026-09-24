from __future__ import annotations

import json
from dataclasses import dataclass

import httpx
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Order, OrderItem, ProductVariant
from ..provider_models import ProviderCommand
from ..reverse_logistics_models import (
    ReturnLogisticsCase,
    ReturnLogisticsEvent,
    ReturnLogisticsItem,
)
from .moysklad_outbound import (
    MoySkladDependencyPending,
    MoySkladReviewRequired,
    _allocate_net_line_totals,
    _base_document,
    _entity_meta,
    _load_order_items,
    _request_json,
    _require_clean_export_session,
    _require_export_configuration,
    _resolve_assortment_meta,
    _snapshot_order,
    _sync_id,
)
from .provider_commands import enqueue_provider_command
from .runtime_capabilities import moysklad_execution_enabled

_ALLOCATION_VERSION = 2
_ALLOCATION_BASIS = "physical_case_completion_v1"
_COMMAND_TYPE = "moysklad.physical_sales_return.create"
_DISPOSITIONS = ("resalable", "damaged", "quarantine")
_DISPOSITION_COMMAND_TYPES = {
    "resalable": _COMMAND_TYPE,
    "damaged": "moysklad.physical_sales_return.damaged.create",
    "quarantine": "moysklad.physical_sales_return.quarantine.create",
}
_AMBIGUOUS_TRANSPORT_ERRORS = (
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.RemoteProtocolError,
)


@dataclass(frozen=True)
class _PhysicalReturnLine:
    order_item_id: int
    moysklad_id: str
    quantity: int
    net_total_cents: int


@dataclass(frozen=True)
class _PhysicalReturnSnapshot:
    case_id: int
    return_request_id: int
    order_snapshot: object
    demand_external_id: str
    disposition: str
    store_id: str
    lines: tuple[_PhysicalReturnLine, ...]


@dataclass(frozen=True)
class _QuarantineMoveSnapshot:
    event_id: int
    case_id: int
    moysklad_id: str
    quantity: int
    target_disposition: str
    source_store_id: str
    target_store_id: str


def _command_key(
    case_id: int,
    disposition: str = "resalable",
    *,
    separated: bool = False,
) -> str:
    if disposition == "resalable" and not separated:
        # Preserve the historical identity only for genuinely all-resalable
        # cases. Mixed legacy review_required commands must never be silently
        # reinterpreted as a partial resalable export.
        return f"physical-return:{int(case_id)}:sales_return:v1"
    version = "v2" if disposition == "resalable" else "v1"
    return f"physical-return:{int(case_id)}:{disposition}:sales_return:{version}"


def _quarantine_move_key(event_id: int) -> str:
    return f"physical-return-quarantine:{int(event_id)}:move:v1"


def _disposition_store_id(disposition: str) -> str:
    settings = get_settings()
    values = {
        "resalable": settings.moysklad_store_id,
        "damaged": settings.moysklad_damaged_store_id,
        "quarantine": settings.moysklad_quarantine_store_id,
    }
    store_id = str(values.get(disposition) or "").strip()
    if not store_id:
        raise MoySkladReviewRequired(
            f"MoySklad {disposition} disposition store is not configured"
        )
    return store_id


def _allocate_remaining_cents(
    *,
    remaining_cents: int,
    current_qty: int,
    remaining_qty: int,
) -> int:
    if remaining_qty <= 0 or current_qty <= 0 or current_qty > remaining_qty:
        raise MoySkladReviewRequired("Physical return allocation quantity is invalid")
    if remaining_cents < 0:
        raise MoySkladReviewRequired("Physical return allocation amount is invalid")
    if current_qty == remaining_qty:
        return int(remaining_cents)
    allocated = int(
        (
            Decimal(remaining_cents)
            * Decimal(current_qty)
            / Decimal(remaining_qty)
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    if allocated < 0 or allocated > remaining_cents:
        raise MoySkladReviewRequired("Physical return monetary allocation is invalid")
    return allocated


def _raw_order_lines(db: Session, order: Order) -> tuple[list[OrderItem], dict[int, int]]:
    items = (
        db.query(OrderItem)
        .filter(OrderItem.order_id == order.id)
        .order_by(OrderItem.id.asc())
        .all()
    )
    if not items:
        raise MoySkladReviewRequired(f"Order {order.id} has no items")
    for item in items:
        if isinstance(item.quantity, bool) or not isinstance(item.quantity, int) or item.quantity <= 0:
            raise MoySkladReviewRequired(f"Order item {item.id} has invalid quantity")
    # Monetary allocation does not depend on provider catalog mappings. Keeping
    # it independent means missing MoySklad assortment data can never roll back
    # warehouse-confirmed physical truth; mapping failures are handled by the
    # outbound worker after the immutable allocation has been persisted.
    synthetic_loaded = [(item, None, None) for item in items]
    line_totals = _allocate_net_line_totals(order, synthetic_loaded)  # type: ignore[arg-type]
    return items, {
        int(item.id): int(line_total)
        for item, line_total in zip(items, line_totals, strict=True)
    }


def _case_completion_ids(db: Session, order_id: int) -> dict[int, int]:
    inspected_case_ids = [
        int(row[0])
        for row in (
            db.query(ReturnLogisticsCase.id)
            .filter(
                ReturnLogisticsCase.order_id == order_id,
                ReturnLogisticsCase.status == "inspected",
            )
            .all()
        )
    ]
    if not inspected_case_ids:
        return {}
    rows = (
        db.query(
            ReturnLogisticsEvent.case_id,
            func.max(ReturnLogisticsEvent.id),
        )
        .filter(
            ReturnLogisticsEvent.case_id.in_(inspected_case_ids),
            ReturnLogisticsEvent.event_type == "inspected",
        )
        .group_by(ReturnLogisticsEvent.case_id)
        .all()
    )
    completion = {int(case_id): int(event_id) for case_id, event_id in rows}
    missing = sorted(set(inspected_case_ids) - set(completion))
    if missing:
        raise MoySkladReviewRequired(
            "Inspected physical return case is missing inspection event evidence: "
            + ", ".join(str(case_id) for case_id in missing)
        )
    return completion


def _build_physical_return_allocation(
    db: Session,
    case_id: int,
    *,
    lock_order: bool = True,
) -> dict[str, object]:
    """Build a stable money allocation from physical-completion order.

    Each original order line is allocated sequentially by the event id that
    completed a physical case. Future sibling cases therefore append after the
    current case and cannot change money already reserved for an earlier case.
    The last physical quantity receives the exact remaining cents, eliminating
    independent-rounding drift across multiple partial returns.
    """
    case = db.query(ReturnLogisticsCase).filter(ReturnLogisticsCase.id == case_id).first()
    if case is None:
        raise MoySkladReviewRequired(f"Physical return case {case_id} does not exist")
    if case.status != "inspected":
        raise MoySkladReviewRequired("MoySklad sales return allocation requires completed physical inspection")

    order_query = db.query(Order).filter(Order.id == case.order_id)
    if lock_order:
        order_query = order_query.with_for_update()
    order = order_query.first()
    if order is None:
        raise MoySkladReviewRequired(f"Order {case.order_id} does not exist")

    order_items, line_totals = _raw_order_lines(db, order)
    order_item_by_id = {int(item.id): item for item in order_items}
    completion = _case_completion_ids(db, int(order.id))
    current_completion = completion.get(int(case.id))
    if current_completion is None:
        raise MoySkladReviewRequired("Physical return case has no completion event evidence")

    inspected_case_ids = sorted(completion)
    physical_items = (
        db.query(ReturnLogisticsItem)
        .filter(
            ReturnLogisticsItem.case_id.in_(inspected_case_ids),
            ReturnLogisticsItem.inspected_qty > 0,
        )
        .all()
    )
    by_order_item: dict[int, list[ReturnLogisticsItem]] = {}
    for physical in physical_items:
        order_item_id = int(physical.order_item_id)
        if order_item_id not in order_item_by_id:
            raise MoySkladReviewRequired("Physical return item is not part of the original order")
        if int(physical.case_id) not in completion:
            raise MoySkladReviewRequired("Physical return item has no case completion evidence")
        by_order_item.setdefault(order_item_id, []).append(physical)

    allocations: dict[tuple[int, int], int] = {}
    for order_item_id, sequence in by_order_item.items():
        order_item = order_item_by_id[order_item_id]
        remaining_qty = int(order_item.quantity)
        remaining_cents = int(line_totals[order_item_id])
        for physical in sorted(
            sequence,
            key=lambda row: (completion[int(row.case_id)], int(row.id)),
        ):
            inspected_qty = int(physical.inspected_qty)
            if inspected_qty <= 0 or inspected_qty > remaining_qty:
                raise MoySkladReviewRequired(
                    "Cumulative physical return quantity exceeds original sold quantity"
                )
            allocated = _allocate_remaining_cents(
                remaining_cents=remaining_cents,
                current_qty=inspected_qty,
                remaining_qty=remaining_qty,
            )
            allocations[(int(physical.case_id), int(physical.id))] = allocated
            remaining_qty -= inspected_qty
            remaining_cents -= allocated
        if remaining_qty == 0 and remaining_cents != 0:
            raise MoySkladReviewRequired(
                "Physical return monetary allocation did not reconcile to the original line"
            )

    current_items = (
        db.query(ReturnLogisticsItem)
        .filter(ReturnLogisticsItem.case_id == case.id, ReturnLogisticsItem.inspected_qty > 0)
        .order_by(ReturnLogisticsItem.id.asc())
        .all()
    )
    if not current_items:
        raise MoySkladReviewRequired("Physical return has no inspected items")

    lines: list[dict[str, int]] = []
    auto_exportable = True
    for physical in current_items:
        inspected_qty = int(physical.inspected_qty)
        authorized_qty = int(physical.authorized_qty)
        resalable_qty = int(physical.resalable_qty)
        damaged_qty = int(physical.damaged_qty)
        quarantine_qty = int(physical.quarantine_qty)
        if inspected_qty != authorized_qty:
            raise MoySkladReviewRequired("Physical return inspection is incomplete")
        if resalable_qty + damaged_qty + quarantine_qty != inspected_qty:
            raise MoySkladReviewRequired("Physical return disposition evidence is inconsistent")
        allocated = allocations.get((int(case.id), int(physical.id)))
        if allocated is None:
            raise MoySkladReviewRequired("Physical return monetary allocation is missing")
        if damaged_qty or quarantine_qty or resalable_qty != inspected_qty:
            auto_exportable = False
        lines.append(
            {
                "order_item_id": int(physical.order_item_id),
                "quantity": inspected_qty,
                "resalable_qty": resalable_qty,
                "damaged_qty": damaged_qty,
                "quarantine_qty": quarantine_qty,
                "net_total_cents": int(allocated),
            }
        )

    return {
        "case_id": int(case.id),
        "order_id": int(order.id),
        "allocation_version": _ALLOCATION_VERSION,
        "allocation_basis": _ALLOCATION_BASIS,
        "completion_event_id": int(current_completion),
        "auto_exportable": auto_exportable,
        "lines": lines,
    }


def _decode_allocation_payload(command: ProviderCommand) -> dict[str, object]:
    try:
        payload = json.loads(str(command.payload_json))
    except (TypeError, json.JSONDecodeError) as exc:
        raise MoySkladReviewRequired("Physical return command allocation payload is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise MoySkladReviewRequired("Physical return command allocation payload must be an object")
    return payload


def _persisted_allocation_payload(
    db: Session,
    *,
    case_id: int,
    order_id: int,
    disposition: str = "resalable",
) -> dict[str, object]:
    command_type = _DISPOSITION_COMMAND_TYPES.get(disposition)
    if command_type is None:
        raise MoySkladReviewRequired("Unsupported physical return provider disposition")
    expected = _build_physical_return_allocation(db, case_id, lock_order=False)
    separated = disposition == "resalable" and not bool(expected.get("auto_exportable"))
    candidate_keys = [_command_key(case_id, disposition, separated=separated)]
    if disposition == "resalable" and separated:
        # Inspect legacy v1 only to produce an explicit historical-review error;
        # it is never accepted as the v2 mixed-disposition authority.
        candidate_keys.append(_command_key(case_id, disposition, separated=False))
    command = (
        db.query(ProviderCommand)
        .filter(
            ProviderCommand.provider == "moysklad",
            ProviderCommand.idempotency_key.in_(candidate_keys),
            ProviderCommand.command_type == command_type,
            ProviderCommand.aggregate_type == "return_logistics_case",
            ProviderCommand.aggregate_id == str(int(case_id)),
        )
        .order_by(ProviderCommand.id.desc())
        .first()
    )
    if command is None:
        raise MoySkladReviewRequired(
            "Physical return is missing immutable MoySklad monetary allocation evidence"
        )
    payload = _decode_allocation_payload(command)
    if payload.get("allocation_version") != _ALLOCATION_VERSION:
        raise MoySkladReviewRequired(
            "Physical return command uses an unsupported monetary allocation contract"
        )
    if payload.get("allocation_basis") != _ALLOCATION_BASIS:
        raise MoySkladReviewRequired("Physical return command allocation basis is invalid")
    if int(payload.get("case_id") or 0) != int(case_id) or int(payload.get("order_id") or 0) != int(order_id):
        raise MoySkladReviewRequired("Physical return command allocation ownership mismatch")
    if payload.get("allocation_error"):
        raise MoySkladReviewRequired(
            f"Physical return monetary allocation requires review: {payload['allocation_error']}"
        )
    if (
        disposition == "resalable"
        and not bool(expected.get("auto_exportable"))
        and command.idempotency_key == _command_key(case_id, "resalable", separated=False)
    ):
        raise MoySkladReviewRequired(
            "Legacy mixed-disposition physical return command cannot be replayed; "
            "record separated provider disposition evidence first"
        )
    persisted_disposition = payload.get("provider_disposition")
    persisted_store_id = payload.get("provider_store_id")
    base_payload = {
        key: value
        for key, value in payload.items()
        if key not in {"provider_disposition", "provider_store_id"}
    }
    # Legacy fully-resalable v1 commands have no provider destination fields.
    if persisted_disposition is not None or persisted_store_id is not None:
        if persisted_disposition != disposition or not str(persisted_store_id or "").strip():
            raise MoySkladReviewRequired(
                "Immutable physical return provider disposition evidence is invalid"
            )
    if base_payload != expected:
        raise MoySkladReviewRequired(
            "Immutable physical return monetary allocation does not match physical evidence"
        )
    return payload


def _split_disposition_cents(
    physical: ReturnLogisticsItem,
    allocated_cents: int,
) -> dict[str, int]:
    remaining_qty = int(physical.inspected_qty)
    remaining_cents = int(allocated_cents)
    result: dict[str, int] = {}
    for disposition, quantity in (
        ("resalable", int(physical.resalable_qty)),
        ("damaged", int(physical.damaged_qty)),
        ("quarantine", int(physical.quarantine_qty)),
    ):
        if quantity <= 0:
            result[disposition] = 0
            continue
        cents = _allocate_remaining_cents(
            remaining_cents=remaining_cents,
            current_qty=quantity,
            remaining_qty=remaining_qty,
        )
        result[disposition] = cents
        remaining_cents -= cents
        remaining_qty -= quantity
    if remaining_qty != 0 or remaining_cents != 0:
        raise MoySkladReviewRequired(
            "Physical return disposition monetary split did not reconcile"
        )
    return result


def _prepare_physical_return_snapshot(
    db: Session,
    case_id: int,
    disposition: str = "resalable",
) -> _PhysicalReturnSnapshot:
    _require_clean_export_session(db)
    try:
        case = db.query(ReturnLogisticsCase).filter(ReturnLogisticsCase.id == case_id).first()
        if case is None:
            raise MoySkladReviewRequired(f"Physical return case {case_id} does not exist")
        if case.status != "inspected":
            raise MoySkladReviewRequired("MoySklad sales return requires completed physical inspection")

        order, loaded_items = _load_order_items(db, case.order_id)
        order_snapshot = _snapshot_order(order, loaded_items)
        order_lines: dict[int, tuple[OrderItem, object, object]] = {
            int(item.id): (item, variant, product)
            for item, variant, product in loaded_items
        }

        physical_items = (
            db.query(ReturnLogisticsItem)
            .filter(ReturnLogisticsItem.case_id == case.id, ReturnLogisticsItem.inspected_qty > 0)
            .order_by(ReturnLogisticsItem.id.asc())
            .all()
        )
        if not physical_items:
            raise MoySkladReviewRequired("Physical return has no inspected items")

        if disposition not in _DISPOSITIONS:
            raise MoySkladReviewRequired("Unsupported physical return provider disposition")
        for physical in physical_items:
            inspected_qty = int(physical.inspected_qty)
            authorized_qty = int(physical.authorized_qty)
            resalable_qty = int(physical.resalable_qty)
            damaged_qty = int(physical.damaged_qty)
            quarantine_qty = int(physical.quarantine_qty)
            if inspected_qty != authorized_qty:
                raise MoySkladReviewRequired("Physical return inspection is incomplete")
            if resalable_qty + damaged_qty + quarantine_qty != inspected_qty:
                raise MoySkladReviewRequired("Physical return disposition evidence is inconsistent")

        allocation = _persisted_allocation_payload(
            db,
            case_id=int(case.id),
            order_id=int(order.id),
            disposition=disposition,
        )
        raw_lines = allocation.get("lines")
        if not isinstance(raw_lines, list):
            raise MoySkladReviewRequired("Physical return monetary allocation lines are invalid")
        allocation_by_item: dict[int, dict[str, object]] = {}
        for raw in raw_lines:
            if not isinstance(raw, dict):
                raise MoySkladReviewRequired("Physical return monetary allocation line is invalid")
            order_item_id = int(raw.get("order_item_id") or 0)
            if order_item_id <= 0 or order_item_id in allocation_by_item:
                raise MoySkladReviewRequired("Physical return monetary allocation line identity is invalid")
            allocation_by_item[order_item_id] = raw

        lines: list[_PhysicalReturnLine] = []
        expected_item_ids: set[int] = set()
        for physical in physical_items:
            loaded = order_lines.get(int(physical.order_item_id))
            if loaded is None:
                raise MoySkladReviewRequired("Physical return item is not part of the original order")
            order_item, variant, product = loaded
            if int(order_item.variant_id) != int(physical.variant_id):
                raise MoySkladReviewRequired("Physical return variant differs from original order")
            moysklad_id = str(variant.moysklad_id or "").strip()
            if not moysklad_id:
                raise MoySkladReviewRequired("Physical return variant has no exact MoySklad assortment mapping")

            expected_item_ids.add(int(order_item.id))
            allocated = allocation_by_item.get(int(order_item.id))
            if allocated is None:
                raise MoySkladReviewRequired("Physical return monetary allocation line is missing")
            try:
                allocated_qty = int(allocated.get("quantity") or 0)
                allocated_resalable = int(allocated.get("resalable_qty") or 0)
                allocated_damaged = int(allocated.get("damaged_qty") or 0)
                allocated_quarantine = int(allocated.get("quarantine_qty") or 0)
                allocated_cents = int(allocated.get("net_total_cents"))
            except (TypeError, ValueError) as exc:
                raise MoySkladReviewRequired("Physical return monetary allocation line is invalid") from exc
            if (
                allocated_qty != int(physical.inspected_qty)
                or allocated_resalable != int(physical.resalable_qty)
                or allocated_damaged != int(physical.damaged_qty)
                or allocated_quarantine != int(physical.quarantine_qty)
            ):
                raise MoySkladReviewRequired("Physical return monetary allocation quantity mismatch")
            if allocated_cents < 0:
                raise MoySkladReviewRequired("Physical return monetary allocation amount is invalid")
            quantity = {
                "resalable": int(physical.resalable_qty),
                "damaged": int(physical.damaged_qty),
                "quarantine": int(physical.quarantine_qty),
            }[disposition]
            if quantity <= 0:
                continue
            split_cents = _split_disposition_cents(physical, allocated_cents)[disposition]
            lines.append(
                _PhysicalReturnLine(
                    order_item_id=int(order_item.id),
                    moysklad_id=moysklad_id,
                    quantity=quantity,
                    net_total_cents=split_cents,
                )
            )
        if set(allocation_by_item) != expected_item_ids:
            raise MoySkladReviewRequired("Physical return monetary allocation contains unexpected lines")
        if not lines:
            raise MoySkladReviewRequired(
                f"Physical return has no {disposition} quantity to export"
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
            disposition=disposition,
            store_id=str(allocation.get("provider_store_id") or _disposition_store_id(disposition)).strip(),
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


async def export_physical_sales_return(
    db: Session,
    case_id: int,
    disposition: str = "resalable",
) -> str:
    _require_export_configuration()
    snapshot = _prepare_physical_return_snapshot(db, case_id, disposition)
    positions = await _physical_return_positions(snapshot)
    sync_id = _sync_id(f"physical-salesreturn-{snapshot.disposition}", snapshot.case_id)
    payload = _base_document(snapshot.order_snapshot, positions, sync_id)
    payload["store"] = _entity_meta("store", snapshot.store_id)
    payload["externalCode"] = (
        f"FLASHIN-PHYSICAL-RETURN-{snapshot.case_id}-{snapshot.disposition.upper()}"
    )
    payload["demand"] = {
        "meta": {
            "href": f"{get_settings().moysklad_base_url.rstrip('/')}/entity/demand/{snapshot.demand_external_id}",
            "type": "demand",
            "mediaType": "application/json",
        }
    }
    payload["description"] = (
        f"FLASHIN physical return case #{snapshot.case_id}; "
        f"return_request=#{snapshot.return_request_id}; "
        f"verified disposition={snapshot.disposition}; store={snapshot.store_id}"
    )[:4096]
    try:
        result = await _request_json("POST", "entity/salesreturn", json_body=payload)
    except _AMBIGUOUS_TRANSPORT_ERRORS as exc:
        # A POST may have reached MoySklad even though the response did not.
        # Never blind-replay this non-idempotent document creation.
        raise MoySkladReviewRequired(
            "MoySklad physical return outcome is ambiguous; reconcile by externalCode before replay"
        ) from exc
    external_id = str(result.get("id") or "").strip()
    if not external_id:
        raise MoySkladReviewRequired("MoySklad physical sales return returned no id")
    return external_id


def enqueue_moysklad_physical_sales_return(db: Session, case_id: int):
    settings = get_settings()
    if not moysklad_execution_enabled(settings) or not settings.moysklad_order_export_enabled:
        return None

    try:
        base_payload = _build_physical_return_allocation(db, int(case_id), lock_order=True)
    except MoySkladReviewRequired as exc:
        # Physical inspection is warehouse truth and must not be rolled back by
        # provider-side mapping or monetary-data defects. Persist the command in
        # a fail-closed form so the worker/operator sees durable review evidence.
        failed_case = (
            db.query(ReturnLogisticsCase)
            .filter(ReturnLogisticsCase.id == int(case_id))
            .first()
        )
        if failed_case is None:
            raise
        base_payload = {
            "case_id": int(case_id),
            "order_id": int(failed_case.order_id),
            "allocation_version": _ALLOCATION_VERSION,
            "allocation_basis": _ALLOCATION_BASIS,
            "allocation_error": str(exc)[:1000],
        }
        return enqueue_provider_command(
            db,
            provider="moysklad",
            command_type=_COMMAND_TYPE,
            idempotency_key=_command_key(case_id, "resalable"),
            aggregate_type="return_logistics_case",
            aggregate_id=case_id,
            payload=base_payload,
        )

    commands = []
    raw_lines = base_payload.get("lines")
    if not isinstance(raw_lines, list):
        raise MoySkladReviewRequired("Physical return allocation lines are invalid")
    for disposition in _DISPOSITIONS:
        quantity = sum(
            int(line.get(f"{disposition}_qty") or 0)
            for line in raw_lines
            if isinstance(line, dict)
        )
        if quantity <= 0:
            continue
        separated = disposition == "resalable" and not bool(base_payload.get("auto_exportable"))
        key = _command_key(case_id, disposition, separated=separated)
        existing = (
            db.query(ProviderCommand)
            .filter(
                ProviderCommand.provider == "moysklad",
                ProviderCommand.idempotency_key == key,
            )
            .first()
        )
        if existing is not None:
            commands.append(existing)
            continue
        payload = {
            **base_payload,
            "provider_disposition": disposition,
            "provider_store_id": _disposition_store_id(disposition),
        }
        commands.append(
            enqueue_provider_command(
                db,
                provider="moysklad",
                command_type=_DISPOSITION_COMMAND_TYPES[disposition],
                idempotency_key=key,
                aggregate_type="return_logistics_case",
                aggregate_id=case_id,
                payload=payload,
            )
        )
    if not commands:
        raise MoySkladReviewRequired("Physical return has no provider disposition quantity")
    return commands[0]


def enqueue_moysklad_quarantine_move(db: Session, event_id: int):
    settings = get_settings()
    if not moysklad_execution_enabled(settings) or not settings.moysklad_order_export_enabled:
        return None
    event = (
        db.query(ReturnLogisticsEvent)
        .filter(
            ReturnLogisticsEvent.id == int(event_id),
            ReturnLogisticsEvent.event_type == "reclassified",
        )
        .first()
    )
    if event is None or event.disposition not in {"resalable", "damaged"}:
        raise MoySkladReviewRequired("Quarantine resolution event is invalid")
    item = db.query(ReturnLogisticsItem).filter(ReturnLogisticsItem.id == event.item_id).first()
    if item is None or int(item.case_id) != int(event.case_id):
        raise MoySkladReviewRequired("Quarantine resolution item ownership is invalid")
    variant = db.query(ProductVariant).filter(ProductVariant.id == item.variant_id).first()
    if variant is None or not str(variant.moysklad_id or "").strip():
        raise MoySkladReviewRequired("Quarantine resolution has no exact MoySklad variant identity")

    key = _quarantine_move_key(event.id)
    existing = (
        db.query(ProviderCommand)
        .filter(
            ProviderCommand.provider == "moysklad",
            ProviderCommand.idempotency_key == key,
        )
        .first()
    )
    if existing is not None:
        return existing
    payload = {
        "event_id": int(event.id),
        "case_id": int(event.case_id),
        "item_id": int(item.id),
        "variant_id": int(item.variant_id),
        "moysklad_id": str(variant.moysklad_id).strip(),
        "quantity": int(event.quantity),
        "target_disposition": str(event.disposition),
        "source_store_id": _disposition_store_id("quarantine"),
        "target_store_id": _disposition_store_id(str(event.disposition)),
        "event_payload_hash": str(event.payload_hash),
    }
    return enqueue_provider_command(
        db,
        provider="moysklad",
        command_type="moysklad.quarantine_move.create",
        idempotency_key=key,
        aggregate_type="return_logistics_event",
        aggregate_id=event.id,
        payload=payload,
    )


def _prepare_quarantine_move_snapshot(db: Session, event_id: int) -> _QuarantineMoveSnapshot:
    _require_clean_export_session(db)
    try:
        command = (
            db.query(ProviderCommand)
            .filter(
                ProviderCommand.provider == "moysklad",
                ProviderCommand.command_type == "moysklad.quarantine_move.create",
                ProviderCommand.idempotency_key == _quarantine_move_key(event_id),
                ProviderCommand.aggregate_type == "return_logistics_event",
                ProviderCommand.aggregate_id == str(int(event_id)),
            )
            .first()
        )
        if command is None:
            raise MoySkladReviewRequired("Quarantine move is missing immutable provider evidence")
        payload = _decode_allocation_payload(command)
        event = db.query(ReturnLogisticsEvent).filter(ReturnLogisticsEvent.id == int(event_id)).first()
        if (
            event is None
            or event.event_type != "reclassified"
            or event.disposition not in {"resalable", "damaged"}
            or int(payload.get("event_id") or 0) != int(event.id)
            or int(payload.get("case_id") or 0) != int(event.case_id)
            or int(payload.get("quantity") or 0) != int(event.quantity)
            or str(payload.get("target_disposition") or "") != str(event.disposition)
            or str(payload.get("event_payload_hash") or "") != str(event.payload_hash)
        ):
            raise MoySkladReviewRequired("Quarantine move evidence no longer matches local authority")
        item = db.query(ReturnLogisticsItem).filter(ReturnLogisticsItem.id == event.item_id).first()
        if item is None or int(item.case_id) != int(event.case_id):
            raise MoySkladReviewRequired("Quarantine move item ownership is invalid")
        variant = db.query(ProductVariant).filter(ProductVariant.id == item.variant_id).first()
        if (
            variant is None
            or str(variant.moysklad_id or "").strip() != str(payload.get("moysklad_id") or "").strip()
        ):
            raise MoySkladReviewRequired("Quarantine move variant identity changed")
        source_store = str(payload.get("source_store_id") or "").strip()
        target_store = str(payload.get("target_store_id") or "").strip()
        if not source_store or not target_store or source_store == target_store:
            raise MoySkladReviewRequired("Quarantine move store evidence is invalid")
        return _QuarantineMoveSnapshot(
            event_id=int(event.id),
            case_id=int(event.case_id),
            moysklad_id=str(variant.moysklad_id).strip(),
            quantity=int(event.quantity),
            target_disposition=str(event.disposition),
            source_store_id=source_store,
            target_store_id=target_store,
        )
    finally:
        if db.in_transaction():
            db.rollback()


async def export_quarantine_move(db: Session, event_id: int) -> str:
    _require_export_configuration()
    snapshot = _prepare_quarantine_move_snapshot(db, event_id)
    assortment = await _resolve_assortment_meta(snapshot.moysklad_id)
    payload = {
        "organization": _entity_meta("organization", get_settings().moysklad_organization_id),
        "sourceStore": _entity_meta("store", snapshot.source_store_id),
        "targetStore": _entity_meta("store", snapshot.target_store_id),
        "syncId": _sync_id("physical-quarantine-move", snapshot.event_id),
        "externalCode": f"FLASHIN-QUARANTINE-MOVE-{snapshot.event_id}",
        "description": (
            f"FLASHIN quarantine resolution event #{snapshot.event_id}; "
            f"case=#{snapshot.case_id}; target={snapshot.target_disposition}"
        )[:4096],
        "positions": [{
            "assortment": assortment,
            "quantity": snapshot.quantity,
            "price": 0,
        }],
    }
    try:
        result = await _request_json("POST", "entity/move", json_body=payload)
    except _AMBIGUOUS_TRANSPORT_ERRORS as exc:
        raise MoySkladReviewRequired(
            "MoySklad quarantine move outcome is ambiguous; reconcile by externalCode before replay"
        ) from exc
    external_id = str(result.get("id") or "").strip()
    if not external_id:
        raise MoySkladReviewRequired("MoySklad quarantine move returned no id")
    return external_id
