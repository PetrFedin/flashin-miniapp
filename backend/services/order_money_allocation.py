from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable, Protocol

_MONEY = Decimal("0.01")
ORDER_MONEY_POLICY_VERSION = 1


class OrderMoneyAllocationError(ValueError):
    """Stored order money cannot be reconciled into authoritative components."""


class _OrderLike(Protocol):
    discount_amount: object
    loyalty_discount_amount: object
    delivery_price: object
    total_amount: object


class _ItemLike(Protocol):
    id: int
    price: object
    quantity: int


@dataclass(frozen=True)
class OrderLineMoney:
    order_item_id: int
    quantity: int
    gross_cents: int
    net_cents: int


@dataclass(frozen=True)
class OrderMoneyAllocation:
    policy_version: int
    order_total_cents: int
    merchandise_cents: int
    delivery_cents: int
    discount_cents: int
    loyalty_cents: int
    lines: tuple[OrderLineMoney, ...]

    def line_cents(self) -> dict[int, int]:
        return {line.order_item_id: line.net_cents for line in self.lines}


def money_cents(value: object, field: str) -> int:
    try:
        amount = Decimal(str(value)).quantize(_MONEY, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise OrderMoneyAllocationError(f"Invalid {field}") from exc
    if not amount.is_finite() or amount < 0:
        raise OrderMoneyAllocationError(f"Invalid {field}")
    return int((amount * 100).to_integral_value(rounding=ROUND_HALF_UP))


def allocate_order_money(
    order: _OrderLike,
    items: Iterable[_ItemLike],
) -> OrderMoneyAllocation:
    ordered_items = list(items)
    if not ordered_items:
        raise OrderMoneyAllocationError("Order has no merchandise lines")

    gross_lines: list[int] = []
    seen_ids: set[int] = set()
    for item in ordered_items:
        item_id = int(item.id)
        if item_id in seen_ids:
            raise OrderMoneyAllocationError("Order contains duplicate item identity")
        seen_ids.add(item_id)
        if isinstance(item.quantity, bool) or not isinstance(item.quantity, int) or item.quantity <= 0:
            raise OrderMoneyAllocationError(f"Order item {item_id} has invalid quantity")
        unit_cents = money_cents(item.price, "order item price")
        gross = unit_cents * int(item.quantity)
        if gross <= 0:
            raise OrderMoneyAllocationError(f"Order item {item_id} gross value must be positive")
        gross_lines.append(gross)

    gross_total = sum(gross_lines)
    if gross_total <= 0:
        raise OrderMoneyAllocationError("Order merchandise total must be positive")

    discount = money_cents(order.discount_amount, "order discount")
    loyalty = money_cents(order.loyalty_discount_amount, "loyalty discount")
    delivery = money_cents(order.delivery_price, "delivery price")
    order_total = money_cents(order.total_amount, "order total")

    merchandise_target = gross_total - discount - loyalty
    if merchandise_target < 0 or merchandise_target + delivery != order_total:
        raise OrderMoneyAllocationError(
            "Order monetary breakdown does not reconcile"
        )

    remaining_target = merchandise_target
    remaining_gross = gross_total
    net_lines: list[int] = []
    for index, gross in enumerate(gross_lines):
        if index == len(gross_lines) - 1:
            allocated = remaining_target
        else:
            allocated = (gross * remaining_target) // remaining_gross
        if allocated < 0 or allocated > gross:
            raise OrderMoneyAllocationError("Order discount allocation is invalid")
        net_lines.append(int(allocated))
        remaining_target -= allocated
        remaining_gross -= gross

    if sum(net_lines) != merchandise_target:
        raise OrderMoneyAllocationError("Order discount allocation did not reconcile")

    return OrderMoneyAllocation(
        policy_version=ORDER_MONEY_POLICY_VERSION,
        order_total_cents=order_total,
        merchandise_cents=merchandise_target,
        delivery_cents=delivery,
        discount_cents=discount,
        loyalty_cents=loyalty,
        lines=tuple(
            OrderLineMoney(
                order_item_id=int(item.id),
                quantity=int(item.quantity),
                gross_cents=int(gross),
                net_cents=int(net),
            )
            for item, gross, net in zip(
                ordered_items,
                gross_lines,
                net_lines,
                strict=True,
            )
        ),
    )


def allocate_net_line_totals(order: _OrderLike, items: Iterable[_ItemLike]) -> list[int]:
    """Compatibility surface for provider documents; returns cents by item order."""

    return [line.net_cents for line in allocate_order_money(order, items).lines]


def allocate_quantity_cents(
    *,
    line_total_cents: int,
    total_quantity: int,
    quantity: int,
    consumed_quantity: int = 0,
    consumed_cents: int = 0,
) -> int:
    """Deterministically allocate a sequential quantity slice of one line.

    The final remaining quantity receives all remaining cents. This is the same
    rounding rule used by physical return valuation and prevents independent
    partial-return rounding drift.
    """

    total = int(line_total_cents)
    total_qty = int(total_quantity)
    current_qty = int(quantity)
    used_qty = int(consumed_quantity)
    used_cents = int(consumed_cents)
    remaining_qty = total_qty - used_qty
    remaining_cents = total - used_cents

    if total < 0 or used_cents < 0 or used_cents > total:
        raise OrderMoneyAllocationError("Line monetary allocation state is invalid")
    if total_qty <= 0 or used_qty < 0 or used_qty >= total_qty:
        raise OrderMoneyAllocationError("Line quantity allocation state is invalid")
    if current_qty <= 0 or current_qty > remaining_qty:
        raise OrderMoneyAllocationError("Line allocation quantity is invalid")
    if current_qty == remaining_qty:
        return remaining_cents

    allocated = int(
        (
            Decimal(remaining_cents)
            * Decimal(current_qty)
            / Decimal(remaining_qty)
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    if allocated < 0 or allocated > remaining_cents:
        raise OrderMoneyAllocationError("Line quantity monetary allocation is invalid")
    return allocated
