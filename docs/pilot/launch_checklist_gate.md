# FLASHIN final P01-P20 launch checklist gate

This gate closes the gap between automated CI/internal E2E and the controlled real deployment. It must stay **NO-GO** until the deployed Telegram Mini App, YooKassa, MoySklad, fulfillment/admin path and required operator observations have been executed and recorded.

## Why this exists

`browser-e2e` and `integrated-e2e` prove the application and internal stack. Signed live lifecycle evidence proves the required external-provider scenarios. Repository-governance evidence proves the exact promoted release came from a protected `main` commit with the required CI checks. The P01-P20 checklist is the final operator-level proof that the launch path itself was exercised end-to-end on that same production configuration.

The checklist is now a fail-closed admission input. A freshly signed governance admission cannot be bound to the pilot runtime until a valid signed P01-P20 report is attached. The runtime binding includes the final admission hash and the checklist report SHA-256, so changing either requires a fresh pilot state/admission.

## Input contract

Edit `docs/pilot/live_pilot_runner.json` only from deployed observations. Never paste raw Telegram `initData`, bot/payment/provider secrets, access tokens, cookies, or credentials.

For every step set:

- `status`: `pass` for completed steps. Critical steps may only be `pass`. Optional steps may be `pass` or `skip`.
- `observed_at`: RFC 3339 timestamp with timezone.
- `owner`: exact named owner already present in the signed pilot admission approvals.
- `comment`: short sanitized operator note. Optional `skip` requires a meaningful reason.
- `evidence`: for every `pass`, one or more `{ "label": "...", "path": "docs/pilot/evidence/..." }` objects. Evidence files must stay under `docs/pilot/evidence` and are hashed into the report.

All 20 IDs, titles, order and critical flags are immutable in code. Reordering, changing titles, downgrading a critical flag, using stale observations, missing evidence, altered evidence files, altered source JSON, wrong release/configuration, or an invalid signature makes the gate fail.

## Required sequence

1. Deploy the exact release and finish the signed live provider gate and rollback drill.
2. Execute and attach signed live lifecycle evidence.
3. Protect `main` and attach signed repository-governance evidence for the exact promoted commit and required CI checks.
4. Execute P01-P20 on the deployed pilot path and fill `docs/pilot/live_pilot_runner.json` with sanitized evidence.
5. Create and verify the signed checklist report:

   ```bash
   python3 scripts/pilot_launch_checklist.py create
   python3 scripts/pilot_launch_checklist.py verify
   ```

6. Attach it as the final admission evidence and verify the complete chain:

   ```bash
   python3 scripts/pilot_launch_admission.py attach
   python3 scripts/pilot_launch_admission.py verify
   ```

7. Only after the final verifier returns `{"go": true, ...}` initialize the signed 20-order pilot state and arm the explicit Telegram allowlist. The runtime remains limited to exactly 20 accepted orders and must be stopped on any critical failure.

## P01-P20 coverage

The immutable contract covers Telegram entry/auth, catalog/product/cart, optional promo/loyalty/referral, checkout, YooKassa payment creation/completion/webhook, paid order state, stock/reservation, fulfillment, optional support, refund creation/approval, optional loyalty reversal, Admin audit trail, and optional customer notification.

This report is **not** generated in CI and is not a substitute for live provider evidence. It is intentionally impossible to create while the repository checklist is still `todo`.
