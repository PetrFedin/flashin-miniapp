# FLASHIN database lock-order registry

Status: IN_PROGRESS

Audit baseline: `pilot/e2e-hardening-20260808` at `35b3a2ebb9eb10a99eaed38392086cbfd171ea2b`.

This document is the authoritative registry for database row-lock ordering discovered during the production concurrency audit. It records only orders that are supported by code and test evidence. It is intentionally **not** a global lock hierarchy yet.

## Evidence states

- `PROVEN_HARDENED` — a previously reachable inversion was removed or a critical ordering contract was explicitly hardened, with regression tests and a real PostgreSQL concurrency/NOWAIT smoke where applicable.
- `OBSERVED_ONE_WAY` — the current code shows one direction, but the reverse call graph has not yet been exhaustively excluded.
- `POTENTIAL_CYCLE` — opposite lock edges have been observed and still require same-row/call-graph proof before being treated as a production defect.
- `UNVERIFIED` — required by the audit plan but not yet inspected deeply enough to make a safe claim.

A `PROVEN_HARDENED` edge may still be downgraded if later repository evidence reveals another reachable reverse path.

## Proven hardened root-order contracts

| Contract | State | Runtime evidence | Test/concurrency evidence | Tracking |
| --- | --- | --- | --- | --- |
| `Customer -> Cart` | `PROVEN_HARDENED` | `backend/api/cart.py` — customer lock precedes active cart locks in `get_or_create_cart`; referral mutation reacquires Customer before Cart | `backend/tests/test_cart_referral_lock_order.py`; `scripts/cart_referral_checkout_lock_order_smoke.py` | PR #195 |
| `Order -> PaymentCreationAttempt` | `PROVEN_HARDENED` | `backend/services/payment_creation.py` — `begin_payment_creation` locks Order before reading/locking the latest creation attempt | `backend/tests/test_payment_creation_lock_order.py`; `scripts/payment_creation_finalize_lock_order_smoke.py` | PR #198 |
| `Order -> Payment` | `PROVEN_HARDENED` | `backend/services/payment_settlement.py` and payment finalization/reconciliation paths use the Order as the root before Payment state mutation | `backend/tests/test_payment_settlement_lock_order.py`; `scripts/checkout_settlement_lock_order_smoke.py`; reconciliation transaction-boundary regression coverage | PRs #194, #199 |
| `Order -> ReturnRequest` | `PROVEN_HARDENED` | `backend/services/refund_locking.py`; return/refund API and webhook paths use Order-first root locking with relationship revalidation | `backend/tests/test_refund_return_lock_order.py`; `backend/tests/test_refund_terminal_state_integrity.py`; `scripts/refund_return_lock_order_smoke.py` | PR #200 |
| `Order -> FulfillmentTask` | `PROVEN_HARDENED` | `backend/services/fulfillment_locking.py`; generic fulfillment update snapshots `task.order_id`, locks Order, then Task, then revalidates | `backend/tests/test_fulfillment_lock_order.py`; `scripts/fulfillment_lock_order_smoke.py`; provider integration spine | PR #205 / issue #204 |
| `CrmProfile -> LoyaltyRedemptionHold` | `PROVEN_HARDENED` | `backend/services/cart_adjustments.py`; `backend/services/loyalty.py::redeem_points` lock profile before reserved holds | `backend/tests/test_loyalty_lock_order.py`; `scripts/loyalty_lock_order_smoke.py` | PR #207 / issue #206 |

## Required pair audit matrix

The table below is deliberately conservative. `UNVERIFIED` means no conclusion is being asserted.

| Pair / area | Current evidence state | Observed direction or finding | Principal paths to inspect / evidence | Next action |
| --- | --- | --- | --- | --- |
| `Customer <-> CrmProfile` | `UNVERIFIED` | No global conclusion yet | auth/customer creation, loyalty, referral rewards, admin CRM | Trace all root locks and same-customer mutations |
| `Customer <-> Cart` | `PROVEN_HARDENED` | `Customer -> Cart` | `backend/api/cart.py`; checkout/referral | Keep invariant; audit any new cart roots |
| `Customer <-> LoyaltyRedemptionHold` | `UNVERIFIED` | Hold rows are customer-scoped, but a direct Customer/Hold lock cycle is not yet proven | checkout, cart loyalty, cancellation/refund restoration | Trace same-customer paths |
| `CrmProfile <-> LoyaltyRedemptionHold` | `PROVEN_HARDENED` for checkout/cart redemption roots; wider loyalty audit still in progress | `CrmProfile -> LoyaltyRedemptionHold` | cart adjustments, loyalty redemption | Audit refund/manual-adjustment paths for reverse edges |
| `Cart <-> CartItem` | `OBSERVED_ONE_WAY` | cart endpoints lock Cart before existing CartItem rows | add/update/remove cart item | Search for CartItem-rooted code that later locks Cart |
| `Cart <-> PromoCode` | `OBSERVED_ONE_WAY` | cart is locked before adjustment reconciliation; promo is then locked | `/cart/promo`, cart reconciliation, checkout | Audit promo admin/usage paths for reverse edge |
| `Cart <-> ProductVariant` | `OBSERVED_ONE_WAY` | item mutation paths commonly lock Cart before ProductVariant | add/update item | Compare with inventory/checkout paths before declaring invariant |
| `Order <-> Payment` | `PROVEN_HARDENED` | `Order -> Payment` | payment settlement/reconciliation | Keep root-order contract and audit all provider callbacks |
| `Order <-> PaymentCreationAttempt` | `PROVEN_HARDENED` | `Order -> PaymentCreationAttempt` | payment creation | Keep invariant |
| `Order <-> ReturnRequest` | `PROVEN_HARDENED` | `Order -> ReturnRequest` | approve/recovery/webhook/finalize | Keep invariant |
| `Order <-> FulfillmentTask` | `PROVEN_HARDENED` | `Order -> FulfillmentTask` | fulfillment PATCH, payment settlement task creation | Keep invariant |
| `Order <-> SlaEvent` | `UNVERIFIED` | No safe conclusion yet | fulfillment SLA update, SLA jobs/admin | Trace locks and mutations |
| `Order <-> OrderItem` | `UNVERIFIED` | No global conclusion yet | checkout creation, returns, fulfillment, cancellation | Trace all `OrderItem FOR UPDATE` sites |
| `OrderItem <-> ProductVariant` | `UNVERIFIED` | No global conclusion yet | checkout reservation, fulfillment, returns, inventory | Verify deterministic variant ordering and reverse paths |
| `FulfillmentTask <-> FulfillmentTaskItem` | `UNVERIFIED` | No global conclusion yet | task item update, picking/packing | Trace task-item locks |
| `FulfillmentTaskItem <-> OrderItem` | `UNVERIFIED` | No global conclusion yet | picking/packing | Trace same-row paths and ordering |
| `ReturnRequest <-> ProductVariant` | `UNVERIFIED` | No global conclusion yet | refund inventory restoration, returns | Trace return finalize/reconciliation |
| `Payment <-> Refund` | `UNVERIFIED` | No global conclusion yet | refund creation/provider webhook/reconciliation | Trace provider refund model and payment locks |
| `Customer <-> LoyaltyTransaction` | `UNVERIFIED` | No global conclusion yet | earn/redeem/refund/manual adjustment/referral | Trace transaction rows and profile/customer roots |
| `Product <-> ProductVariant` | `UNVERIFIED` | No global conclusion yet | catalog admin, inventory, import/sync | Audit mutations and multi-row ordering |
| `Product <-> pricing publication` | `UNVERIFIED` | No global conclusion yet | pricing publication/version services | Identify exact model/table and lock sites |
| `Webhook outbox <-> domain rows` | `UNVERIFIED` | No global conclusion yet | payment/refund/domain event enqueue and workers | Audit producer/worker lock direction |

## Active audit findings that are not yet defects

### Loyalty refund path

`backend/services/loyalty.py::refund_redeemed_points` must be treated as an active audit target. The currently observed local sequence includes a lock on an existing `LoyaltyTransaction`, then a `LoyaltyRedemptionHold`, followed by `add_points(...)`, which locks `CrmProfile`.

This creates a **local reverse edge** relative to the proven redemption contract `CrmProfile -> LoyaltyRedemptionHold`, but it is **not yet classified as a production deadlock**. Before opening a P0 defect, the audit must prove that the opposite path can acquire the same rows in a concurrent transaction and can complete a wait cycle. Until then the finding remains `POTENTIAL_CYCLE` for investigation, not a confirmed incident.

Required proof before escalation:

1. enumerate every caller of `refund_redeemed_points`;
2. identify the enclosing root locks and transaction boundary;
3. identify an opposite path that locks the same profile/hold/transaction rows;
4. reproduce the wait cycle in real PostgreSQL;
5. only then create a dedicated issue/branch/PR and NOWAIT smoke.

## Deterministic multi-row locking rule

Where a transaction locks multiple `ProductVariant` rows, acquisition must be deterministic (normally ascending `ProductVariant.id` or an equivalent sorted-ID contract). The audit has not yet certified every variant-locking path, so this is a required rule, not a claim that the whole repository already conforms.

The same principle applies to any multi-row lock set: derive a stable key set first and acquire rows in a deterministic order.

## Failure semantics for root/child locking helpers

For hardened root/child relationships such as Order/ReturnRequest and Order/FulfillmentTask:

1. take a non-locking snapshot only to discover the root id when the child id is the API input;
2. lock the root row first;
3. lock the child row second;
4. revalidate the relationship after both locks;
5. fail closed with `409 Conflict` when the relationship changed or the root disappeared;
6. do not let a downstream service silently reacquire the root in the reverse order.

## Audit sequence

The remaining database-concurrency audit proceeds in this order:

1. loyalty refund / manual adjustment / cancellation restoration;
2. Cart / CartItem / PromoCode / ProductVariant mutation graph;
3. Order / OrderItem / ProductVariant graph;
4. fulfillment item / order item / SLA graph;
5. payment / refund graph;
6. product / variant / pricing graph;
7. webhook inbox/outbox and domain-row graph;
8. background jobs and reconciliation workers;
9. repository-wide second pass over every `.with_for_update()` and raw `FOR UPDATE` occurrence.

Every confirmed inversion gets its own issue -> branch -> PR -> regression test -> real PostgreSQL concurrency proof -> full CI -> Security. Independent inversions are not bundled into this documentation PR.

## Change control

- Do not promote `OBSERVED_ONE_WAY`, `POTENTIAL_CYCLE`, or `UNVERIFIED` to `PROVEN_HARDENED` without code/test evidence.
- If a new reverse path is found for a hardened pair, immediately downgrade the status and open a dedicated defect.
- Any PR introducing a new `with_for_update()` must state the predecessor/root lock and must not contradict this registry.
- This document must be updated when a lock-order hardening PR is merged into `pilot/e2e-hardening-20260808`.
