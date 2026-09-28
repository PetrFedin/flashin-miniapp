# MoySklad reverse-logistics disposition authority

## Purpose

FLASHIN treats financial refund settlement and physical inventory as separate authorities. A payment refund never restores sellable stock. Sellable inventory can increase only from verified physical reverse-logistics evidence.

For any enabled MoySklad mode, `MOYSKLAD_STORE_ID` is mandatory and is the only warehouse allowed to drive storefront sellable stock.

When live MoySklad order/reverse-logistics export is enabled, three distinct warehouses are mandatory:

- `MOYSKLAD_STORE_ID` — sellable stock used by the storefront.
- `MOYSKLAD_DAMAGED_STORE_ID` — terminal non-sellable damaged stock.
- `MOYSKLAD_QUARANTINE_STORE_ID` — temporary non-sellable quarantine stock.

The three IDs must be different. Production configuration fails closed if enabled MoySklad has no sellable store, and fails closed on live outbound execution unless all three disposition stores are configured.

## Initial physical return

After warehouse inspection, each returned quantity has exactly one immutable initial disposition: `resalable`, `damaged`, or `quarantine`.

FLASHIN creates separate durable provider commands for every non-zero disposition:

- resalable → customer SalesReturn into the sellable store;
- damaged → customer SalesReturn into the damaged store;
- quarantine → customer SalesReturn into the quarantine store.

A mixed return such as 1 resalable + 1 damaged + 1 quarantine therefore produces three provider inventory outcomes. The original inspection evidence remains append-only even if quarantine is resolved later.

Historical mixed-return v1 commands are never silently reinterpreted. Mixed resalable export uses a new v2 idempotency identity and requires explicit separated disposition evidence.

## Sellable inbound stock authority

The storefront never imports aggregate MoySklad `effectiveStock` for an enabled production MoySklad integration.

FLASHIN requests `GET /report/stock/bystore` with `groupBy=variant`, resolves each assortment provider ID, and applies only the `stock` belonging to `MOYSKLAD_STORE_ID`.

Damaged and quarantine store quantities therefore cannot re-enter `ProductVariant.stock_qty` through the next provider synchronization.

## Quarantine resolution

Quarantine is not terminal until an explicit operator decision resolves it to either:

- `resalable`; or
- `damaged`.

When live MoySklad execution is enabled, reclassification is blocked until the original quarantine SalesReturn is confirmed as sent with an external provider document ID.

The local reclassification is represented by one append-only `reclassified` event and one idempotent inventory mutation. FLASHIN then queues one MoySklad Move:

- source: quarantine store;
- target: sellable or damaged store;
- quantity: exactly the reclassified quantity.

A quarantine-to-resalable move increases local sellable stock exactly once. Stock authority protects that locally verified increase until the corresponding provider Move is confirmed and an inbound sellable-store snapshot catches up.

## Ambiguous provider outcomes

SalesReturn and Move creation are non-idempotent external POST operations from FLASHIN's point of view.

If transport fails after the request may have reached MoySklad (for example read timeout, write timeout, or remote protocol loss), FLASHIN marks the provider command `review_required`. It does not blindly replay the POST.

The operator must reconcile the remote document by the deterministic `externalCode` / recorded provider evidence before any controlled replay or manual resolution.

## Operator evidence

The Admin physical-return panel exposes:

- initial quantity by resalable / damaged / quarantine;
- provider command status for every disposition;
- provider external document ID when confirmed;
- last reconciliation error;
- quarantine resolution Move history;
- a visible reconciliation-required warning.

Quarantine can be resolved only through the dedicated action with quantity, target disposition, reason, and a durable browser/server idempotency key.

## Rollback

Alembic revision `0046_moysklad_return_disposition` adds `reclassified` event authority.

Once any reclassification evidence exists, downgrade below 0046 is blocked. Production rollback must restore a verified backup from the target release rather than discard quarantine resolution history.
