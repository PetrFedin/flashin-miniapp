# FLASHIN live lifecycle evidence runbook

## Purpose

Provider connectivity and a green browser test do not prove that the deployed pilot can complete a real customer and operator cycle. This runbook creates a signed, release-bound evidence package that must be attached to the pilot admission before runtime arm.

The gate is deliberately fail-closed. Missing, stale, unsigned, checksum-drifted, cross-order or ownerless evidence keeps the pilot at **NO-GO**.

## Required order of operations

1. Promote and verify different current and previous immutable releases.
2. Deploy the exact current release to the pilot host.
3. Run strict provider probes and retain the signed report.
4. Run the live readiness gate against public HTTPS endpoints.
5. Complete and retain a signed production-like rollback drill.
6. Create the baseline signed pilot admission with named owners.
7. Select one dedicated pilot customer and one controlled variant with no existing reservation.
8. Run the guarded real order/payment E2E. It must create `docs/pilot/evidence/real_order_e2e_context.json` containing the exact controlled order, variant, pre-order stock and YooKassa payment identifier without credentials.
9. Complete the real provider/operator lifecycle for that same order: YooKassa redirect/callback, fulfillment/delivery, return/refund, MoySklad outbound processing and Telegram notification delivery.
10. Run the terminal real lifecycle verifier against the context artifact. It must prove the exact order is refunded/delivered, the same SKU returned to baseline stock, exactly one full provider refund exists, MoySklad `customerorder`/`demand`/`salesreturn` commands are sent with external IDs and exactly one deterministic refund notification is actually sent.
11. Execute every other required deployed lifecycle scenario below.
12. Save sanitized evidence files under `docs/pilot/evidence` on the pilot host or approved secure evidence storage mounted at that repository path.
13. Create the signed lifecycle report.
14. Attach the lifecycle report to the signed admission.
15. Verify the lifecycle attachment.
16. Create fresh signed repository-governance evidence for the exact promoted release commit and successful required CI.
17. Attach repository-governance evidence to the signed admission.
18. Verify the complete admission, lifecycle and governance chain.
19. Complete and attach the signed P01-P20 launch checklist.
20. Initialize the 20-order control state and arm runtime only for the explicit Telegram allowlist after final admission returns `go: true`.

## Required deployed scenarios

Always required:

- `telegram_real_auth` — a real Telegram-signed Mini App session reaches the protected customer API;
- `yookassa_payment_redirect` — the controlled order receives a real sandbox/pilot confirmation redirect;
- `yookassa_payment_return` — the provider returns to the deployed Mini App and the controlled order refreshes correctly;
- `yookassa_duplicate_webhook` — the same provider event is delivered twice without duplicate financial effects;
- `yookassa_refund` — the controlled full refund reaches the expected terminal state and reconciles;
- `moysklad_live_sync` — controlled real products/variants/stocks synchronize without unresolved identity conflicts;
- `moysklad_customerorder_outbound` — the paid controlled order creates or reconciles exactly one expected `customerorder` in MoySklad;
- `moysklad_demand_outbound` — the controlled fulfillment path creates or reconciles exactly one expected `demand` and the stock delta matches the local inventory ledger;
- `moysklad_salesreturn_outbound` — the controlled return/refund path creates or reconciles exactly one expected `salesreturn` and the reverse stock delta matches the local inventory ledger;
- `notification_delivery` — the deterministic refund notification for the controlled order is delivered by Telegram.

Conditionally required:

- `meilisearch_live_index` when `MEILISEARCH_ENABLED=true`;
- `media_live_delivery` when `MEDIA_STORAGE=s3` or `MEDIA_STORAGE=r2`.

The production-host rollback proof remains a separate mandatory signed admission input.

## Controlled-order correlation contract

The following scenarios are one end-to-end transaction and **must all use the exact same `subject_id`** in the form `order:<local-order-id>`:

- `yookassa_payment_redirect`;
- `yookassa_payment_return`;
- `yookassa_duplicate_webhook`;
- `yookassa_refund`;
- `moysklad_customerorder_outbound`;
- `moysklad_demand_outbound`;
- `moysklad_salesreturn_outbound`;
- `notification_delivery`.

Every one of those eight scenarios must also include exactly one evidence entry whose path is:

```text
docs/pilot/evidence/real_order_e2e_context.json
```

Admission requires all eight references to carry the same SHA-256. It also opens the context artifact and verifies schema/kind, YooKassa provider identity, `subject_id == order:<order_id>` and a zero pre-existing reservation baseline. A signed lifecycle report assembled from different orders therefore cannot be attached to pilot admission.

`telegram_real_auth`, `moysklad_live_sync` and conditional search/media scenarios may use their own appropriate subject identifiers.

## Generate the shared real-order context

Use a dedicated pilot customer whose cart is empty and a controlled SKU with zero existing reservations. The runner fails closed if the cart has items, a promo, reserved loyalty points, if the variant is not unique/available, or if its inventory already has reservations.

```bash
API_BASE='https://api.example.test' \
CUSTOMER_TOKEN='...' \
ADMIN_TOKEN='...' \
E2E_VARIANT_ID='456' \
make real-order-e2e
```

The runner writes the sanitized shared context to `docs/pilot/evidence/real_order_e2e_context.json`. It contains identifiers and baseline quantities only; it never stores customer/admin tokens, YooKassa credentials or confirmation URLs.

After the real YooKassa callback, fulfillment/delivery, provider refund, MoySklad processing and Telegram delivery complete for that same order, run:

```bash
API_BASE='https://api.example.test' \
CUSTOMER_TOKEN='...' \
ADMIN_TOKEN='...' \
make real-lifecycle-e2e
```

The terminal verifier reads the shared context automatically. Do not manually substitute another order ID, SKU or stock baseline.

## Lifecycle input file

Create `docs/pilot/live_lifecycle_input.json` outside source control or from the secure operator workstation:

```json
{
  "scenarios": [
    {
      "name": "yookassa_payment_redirect",
      "status": "PASS",
      "observed_at": "2026-08-11T12:00:00Z",
      "owner": "Named Operations Owner",
      "subject_id": "order:123",
      "notes": "Controlled order received the expected YooKassa redirect.",
      "evidence": [
        {
          "label": "shared real-order E2E context",
          "path": "docs/pilot/evidence/real_order_e2e_context.json"
        },
        {
          "label": "sanitized YooKassa redirect evidence",
          "path": "docs/pilot/evidence/yookassa-redirect-sanitized.json"
        }
      ]
    }
  ]
}
```

Repeat the object for every required scenario. The lifecycle builder rejects missing, duplicate and unknown scenario names. Admission then rejects cross-order subjects, missing shared context references or different shared-context checksums across the eight order-linked scenarios.

For the three outbound MoySklad scenarios, provider evidence must carry the sanitized local order/fulfillment/return identifier plus the corresponding MoySklad entity ID or href. The operator must verify one-to-one creation/reconciliation and the expected inventory delta before marking the scenario `PASS`.

## Evidence rules

Each scenario must contain:

- `status` exactly `PASS`;
- a timezone-aware RFC 3339 `observed_at` within the configured freshness window;
- a non-empty `subject_id` identifying the controlled session/order/sync subject;
- a named `owner` equal to one of the signed admission owner names;
- one to ten non-empty, regular evidence files;
- bounded notes that describe the observation without secrets.

The report records a portable repository-relative path and SHA-256 for each file. Any later file modification invalidates admission. The same path must resolve on the host and inside the backend container.

Never store:

- raw Telegram `initData`;
- customer/admin bearer tokens;
- Telegram bot tokens;
- YooKassa secret keys;
- MoySklad passwords or tokens;
- S3/R2 credentials;
- Meilisearch master keys;
- GitHub tokens;
- the pilot evidence signing secret.

Use sanitized JSON, provider IDs, order IDs, timestamps, bounded logs, screenshots without credentials and reconciliation exports without customer-sensitive data.

## Admission commands

Create and sign the lifecycle report:

```bash
make pilot-lifecycle-create ARGS='--input docs/pilot/live_lifecycle_input.json'
```

Attach the report to the current baseline admission and re-sign it. This step enforces the shared controlled-order correlation contract:

```bash
make pilot-lifecycle-attach
```

Verify baseline admission, lifecycle attachment, owners, release/config binding, controlled-order correlation, freshness and every evidence checksum:

```bash
make pilot-lifecycle-status
```

Create repository-governance evidence only after GitHub `main` is protected and required CI has succeeded for the exact promoted release commit. The owner must exactly match the signed `technical_owner`:

```bash
make pilot-governance-create ARGS='--owner "Named Technical Owner"'
make pilot-governance-attach
make pilot-governance-status
```

Create, verify and attach the final P01-P20 checklist, then verify final admission:

```bash
make pilot-checklist-create
make pilot-checklist-status
make pilot-checklist-attach
make pilot-admission-status
```

Only after the final command returns `go: true`:

```bash
make pilot-init ARGS='--operator-role operations_owner --operator "Named Operations Owner" --reason "Initialize controlled 20-order pilot"'
make pilot-runtime-arm ARGS='--telegram-id 123456789'
```

## Automatic rejection conditions

Pilot arm remains blocked when any of the following is true:

- baseline admission is invalid or expired;
- lifecycle acknowledgement or attachment is missing;
- report signature, release or configuration fingerprint does not match;
- report or scenario timestamps are stale or in the future;
- a required scenario is missing, duplicated, unknown or not PASS;
- order-linked scenarios do not share one controlled `order:<id>` subject;
- any order-linked scenario omits the shared real-order context artifact or references a different context SHA-256;
- the shared context artifact has the wrong schema/kind/provider, mismatched order/subject, or a non-zero pre-existing reservation;
- any required MoySklad sync, `customerorder`, `demand` or `salesreturn` observation is missing;
- Meilisearch/media evidence is omitted while the corresponding production feature is enabled;
- a scenario owner is not one of the signed admission owners;
- an evidence file is missing, empty, oversized, modified or contains configured secrets/raw Telegram initData;
- repository-governance evidence is missing, stale, unsigned or owned by someone other than the signed technical owner;
- the protected branch head differs from the promoted release commit;
- pull-request-only changes, strict required checks, force-push/deletion restrictions or administrator/ruleset bypass controls are not active;
- no successful completed `CI` run exists for the exact release commit;
- the signed P01-P20 checklist is missing, invalid or does not bind the exact lifecycle/governance evidence;
- an old pilot state is bound to a different lifecycle, repository-governance or checklist report SHA-256.

Do not repair evidence in place during an active pilot. Stop runtime, record the incident, create fresh evidence and issue a fresh signed admission/control state.
