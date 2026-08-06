# FLASHIN live lifecycle evidence runbook

## Purpose

Provider connectivity and a green browser test do not prove that the deployed pilot can complete a real customer and operator cycle. This runbook creates a signed, release-bound evidence package that must be attached to the pilot admission before runtime arm.

The gate is deliberately fail-closed. Missing, stale, unsigned, checksum-drifted or ownerless evidence keeps the pilot at **NO-GO**.

## Required order of operations

1. Promote and verify different current and previous immutable releases.
2. Deploy the exact current release to the pilot host.
3. Run strict provider probes and retain the signed report.
4. Run the live readiness gate against public HTTPS endpoints.
5. Complete and retain a signed production-like rollback drill.
6. Create the baseline signed pilot admission with named owners.
7. Execute every required deployed lifecycle scenario below.
8. Save sanitized evidence files locally on the pilot host or approved secure evidence storage mounted on the host.
9. Create the signed lifecycle report.
10. Attach the lifecycle report to the signed admission.
11. Verify the complete admission.
12. Initialize the 20-order control state and arm runtime only for the explicit Telegram allowlist.

## Required deployed scenarios

Always required:

- `telegram_real_auth` — a real Telegram-signed Mini App session reaches the protected customer API;
- `yookassa_payment_redirect` — a controlled order receives a real sandbox confirmation redirect;
- `yookassa_payment_return` — the provider returns to the deployed Mini App and the order refreshes correctly;
- `yookassa_duplicate_webhook` — the same provider event is delivered twice without duplicate financial effects;
- `yookassa_refund` — a sandbox refund reaches the expected terminal/review state and reconciles;
- `moysklad_live_sync` — controlled real products/variants/stocks synchronize without unresolved identity conflicts;
- `notification_delivery` — the expected Telegram notification is delivered for the controlled subject.

Conditionally required:

- `meilisearch_live_index` when `MEILISEARCH_ENABLED=true`;
- `media_live_delivery` when `MEDIA_STORAGE=s3` or `MEDIA_STORAGE=r2`.

The production-host rollback proof remains a separate mandatory signed admission input.

## Input file

Create `docs/pilot/live_lifecycle_input.json` outside source control or from the secure operator workstation:

```json
{
  "scenarios": [
    {
      "name": "telegram_real_auth",
      "status": "PASS",
      "observed_at": "2026-08-06T15:00:00Z",
      "owner": "Named Operations Owner",
      "subject_id": "telegram-user-or-controlled-session-id",
      "notes": "Protected customer endpoint returned the expected account.",
      "evidence": [
        {
          "label": "sanitized Telegram auth trace",
          "path": "docs/pilot/evidence/telegram-auth-sanitized.json"
        }
      ]
    }
  ]
}
```

Repeat the object for every required scenario. The script rejects missing, duplicate and unknown scenario names.

## Evidence rules

Each scenario must contain:

- `status` exactly `PASS`;
- a timezone-aware RFC 3339 `observed_at` within the configured freshness window;
- a non-empty `subject_id` identifying the controlled session, order, payment, refund, sync or delivery;
- a named `owner` equal to one of the signed admission owner names;
- one to ten non-empty, regular evidence files;
- bounded notes that describe the observation without secrets.

The report records an absolute path and SHA-256 for each file. Any later file modification invalidates admission.

Never store:

- raw Telegram `initData`;
- Telegram bot tokens;
- YooKassa secret keys;
- MoySklad passwords or tokens;
- S3/R2 credentials;
- Meilisearch master keys;
- the pilot evidence signing secret.

Use sanitized JSON, provider IDs, order IDs, timestamps, bounded logs, screenshots without credentials and reconciliation exports without customer-sensitive data.

## Commands

Create and sign the lifecycle report:

```bash
make pilot-lifecycle-create ARGS='--input docs/pilot/live_lifecycle_input.json'
```

Attach the report to the current baseline admission and re-sign it:

```bash
make pilot-lifecycle-attach
```

Verify baseline admission, attachment, owners, release/config binding, freshness and every evidence checksum:

```bash
make pilot-lifecycle-status
```

Only after the command returns `go: true`:

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
- Meilisearch/media evidence is omitted while the corresponding production feature is enabled;
- a scenario owner is not one of the signed admission owners;
- an evidence file is missing, empty, oversized, modified or contains configured secrets/raw Telegram initData;
- an old pilot state is bound to a different lifecycle report SHA-256.

Do not repair evidence in place during an active pilot. Stop runtime, record the incident, create fresh evidence and issue a fresh signed admission/control state.
