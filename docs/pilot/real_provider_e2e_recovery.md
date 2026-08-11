# FLASHIN interrupted real-provider E2E recovery

## Purpose

The real-order E2E is deliberately side-effectful: it can create a real pilot order and a real YooKassa payment attempt. A process crash, SSH disconnect, timeout or host restart must never make a second payment attempt the default recovery action.

`backend/tests/e2e/test_real_order_flow_runner.py` therefore creates the private marker `docs/pilot/evidence/real_order_e2e_context.json` **before checkout** and advances it durably through three phases:

1. `checkout_intent` — controlled SKU/cart checks passed and the checkout idempotency key was durably persisted before calling checkout;
2. `order_created` — checkout returned one exact controlled order and its local order ID/subject are durably persisted before calling the payment endpoint;
3. `payment_created` — YooKassa payment creation returned a provider payment ID and the full context is eligible for terminal lifecycle verification.

Each marker replacement flushes the file and parent directory with `fsync`. Any existing marker blocks a fresh real-order run.

## Inspect the marker

Run from the exact deployed pilot checkout:

```bash
python3 scripts/real_e2e_context_status.py
```

The command never prints customer/admin bearer tokens, YooKassa secret keys or the stored checkout idempotency key. It exits `0` only for a structurally valid `payment_created` context. Provisional or invalid contexts exit non-zero and include a recovery action.

A custom private path can be inspected with:

```bash
python3 scripts/real_e2e_context_status.py \
  --context docs/pilot/evidence/real_order_e2e_context.json
```

## Recovery by phase

### `checkout_intent`

Do **not** run the real-order E2E again and do not delete the marker first.

The runner may have failed before checkout, during the request, or after the server committed checkout but before the client received the response. Investigate the pilot customer/order records and the context creation timestamp. Where direct database/operator inspection is needed, use read-only access. If an order was created, continue recovery from that exact order; do not create another checkout.

The private context retains the checkout idempotency key specifically so an authorized operator can correlate an ambiguous checkout attempt without generating a new identity. Do not copy that key into public tickets or signed lifecycle evidence.

### `order_created`

The marker contains the exact local `order_id` and `subject_id`, but no confirmed provider payment ID yet. Do **not** start a second order/payment run.

Inspect the exact order and the authoritative YooKassa/payment records. The payment request may have failed before provider creation, or the provider may have created a payment while the response was lost. Resolve the existing order/payment state first. A new payment attempt is allowed only through an explicitly reviewed recovery action for the same order, never by deleting the marker and rerunning the whole customer flow.

### `payment_created`

The payment attempt is durably bound to the exact order and provider ID. Continue only that same controlled lifecycle:

- complete/observe YooKassa confirmation and callback;
- fulfill and deliver the same order;
- create/complete the controlled refund;
- wait for MoySklad outbound `customerorder`, `demand` and `salesreturn` evidence;
- verify the deterministic Telegram refund notification;
- run the terminal read-only real lifecycle verifier;
- collect scenario-specific sanitized evidence and create the signed lifecycle report.

The terminal verifier and pilot admission both reject `checkout_intent` and `order_created`; only `payment_created` can advance into live admission.

## Archiving or clearing a marker

Do not modify the marker in place. Preserve the original as private incident/evidence material when a real external side effect may have occurred.

A marker may be moved out of the active context path only after an accountable operator has established one of the following:

- no order was created and no provider payment exists; or
- the existing order/payment was safely completed/cancelled/reconciled and will not be reused as the next controlled lifecycle; or
- the completed `payment_created` lifecycle has already been fully verified, evidenced and intentionally archived before starting another controlled order.

After archiving, re-run the clean-cart, zero-reservation and controlled-variant preconditions before any new real-payment run.

## Automatic NO-GO conditions

Stop and investigate instead of retrying when:

- the context file exists in a provisional phase;
- the context JSON is invalid or has an unknown phase;
- `api_base` differs from the deployed pilot API;
- the controlled variant or baseline stock data is invalid;
- `order_created` lacks a valid order/subject binding;
- `payment_created` lacks a provider payment ID;
- terminal verification reports a different order/SKU/provider state;
- the signed admission rejects the context or any scenario-specific evidence.

The recovery principle is simple: **an interrupted real-payment attempt is a reconciliation problem, not a reason to create a new payment.**