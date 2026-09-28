# Refund financial item allocation

## Authority boundary

A financial refund does not restore sellable inventory.

FLASHIN persists the monetary composition of every provider refund separately
from physical reverse logistics:

- financial authority: `ReturnRequest` + `ReturnRefundAllocation`;
- physical authority: `ReturnLogisticsCase` / items / events;
- provider stock document: MoySklad SalesReturn built only from completed
  physical inspection evidence.

No financial refund code path writes product stock.

## One order-money policy

`backend/services/order_money_allocation.py` is the only order-level money
allocation policy.

It reconciles:

1. original order-item gross cents;
2. order promo discount;
3. loyalty discount;
4. merchandise net value by original `OrderItem.id`;
5. delivery as a separate component;
6. exact paid order total.

The same policy is consumed by financial refund allocation and MoySklad outbound
documents. The final line receives the exact remaining cents so the allocation
always reconciles to the stored order total.

## Refund components

Each durable allocation row belongs to one `ReturnRequest` and one order.

- `item`: references the exact original `OrderItem.id`.
- `delivery`: refunds paid delivery without pretending it is merchandise.
- `goodwill`: explicit money-only adjustment with no physical item authority.

Amounts are stored as integer cents. The policy version is persisted.

An optional `quantity_evidence` may be attached only to an item component.
When present, its cents must equal the sequential quantity share of the original
net line value. A value-only item allocation cannot later be followed by
quantity evidence for the same sold line, because the remaining unit/value
mapping would be ambiguous.

## Staged partial refunds

Completed or in-flight allocations reserve their component value.

A later ReturnRequest cannot exceed:

- the original net value of an item line;
- the original sold quantity when quantity evidence is used;
- the original paid delivery value;
- the original paid order total.

A failed refund keeps its historical allocation evidence but releases the
capacity for a later request.

The composition is immutable once persisted. A provider retry may reuse the
same evidence, but cannot rewrite it.

## Automatic vs explicit allocation

A request with no explicit components is accepted only when its amount equals
the exact remaining item + delivery value. This preserves the existing
full-remaining-refund workflow.

Partial refunds and goodwill require explicit composition. FLASHIN never guesses
which item a partial amount belongs to.

## Financial / physical reconciliation

Admin operations expose a sanitized order-level result:

- `PASS`: completed item refund value equals completed inspected physical
  return value, with no unresolved stage.
- `PENDING`: a financial request or physical case is still open, or completed
  item money exists before physical evidence.
- `REVIEW`: both sides are terminal but item values differ.
- `BLOCKED`: allocation evidence is corrupt, missing for a completed refund,
  exceeds a monetary cap, or uses an unsupported policy version.

Delivery and goodwill are financial-only components and are not fabricated into
physical-return value.

This reconciliation is evidence/operations state only. It does not authorize
inventory mutations.

## Operator workflow

For a new partial refund, Service Operations requires the refund amount to be
allocated across original items, delivery and/or goodwill before provider
execution. The component sum must equal the provider refund amount to the cent.

If allocation was already fixed before a provider timeout/retry, the UI renders
it read-only and retries the same composition.

The Admin projection shows only business evidence: component amounts,
`OrderItem.id`, reconciliation status/codes and line deltas. Provider secrets
and customer PII are not added by this contract.
