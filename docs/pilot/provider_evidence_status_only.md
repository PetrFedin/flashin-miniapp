# Provider evidence status-only policy — pilot v27

This policy is an additional fail-closed layer on top of the v26 provider probe privacy controls. It does not change the Telegram → YooKassa → webhook → order → stock/MoySklad → fulfillment → refund → notification business lifecycle.

## Rule

Signed provider evidence must contain provider readiness status, not provider output.

The live probe runner may transiently capture bounded/redacted subprocess output while a probe is executing, but `build_report()` discards that output before signing or writing evidence. Each persisted result contains only:

- a bounded probe name from the fixed pilot provider set;
- `ok` as a boolean;
- a numeric process exit code or `null`;
- empty `stdout`;
- empty `stderr`.

An unknown probe name is collapsed to `unknown`, and a non-integer return code is collapsed to `null` before signing. Arbitrary result fields are never copied into newly generated evidence.

## Verification

`scripts/check_integrations.py verify` fails closed when an otherwise signed report contains either:

- non-empty probe `stdout` or `stderr`; or
- unsupported per-result fields.

This means a report from the older persistence format is not reusable for the v27 operational gate if it retained probe output. Generate fresh evidence on the exact deployed release instead of editing or migrating old evidence.

## Why this exists

Redaction is useful defense in depth but cannot enumerate every future provider identifier, PII field, transformed credential, URL encoding, proxy message, or third-party error body. Status-only persistence removes that dependency from signed launch evidence.

v26 remains the first layer: individual Telegram, YooKassa, MoySklad, R2/S3 and Meilisearch probes should still emit bounded safe output. v27 adds the persistence boundary: even an accidentally noisy future probe does not have its stdout/stderr copied into the signed provider report.

## Regression coverage

`backend/tests/test_provider_evidence_status_only.py` verifies that:

1. unknown provider output and URLs are absent from the signed report;
2. unknown result names and non-numeric return codes are bounded;
3. a report that is modified to reintroduce output and then re-signed with a valid test signing secret is rejected by the operational verifier;
4. newly generated status-only evidence passes the status-only policy.

## Live pilot use

On the exact deployed immutable release, generate a fresh report with:

```bash
python scripts/check_integrations.py run --acknowledge-side-effects
```

Then verify without provider side effects:

```bash
python scripts/check_integrations.py verify
```

The YooKassa live probe still creates its documented idempotent 1.00 RUB pending test payment when `run` is explicitly acknowledged. Verification itself is read-only.

A successful CI run proves only the code path. It does not complete Telegram, YooKassa, MoySklad, fulfillment, refund, notification, public HTTPS, or P01–P20 live evidence. Real-money pilot admission remains NO-GO until the signed deployed checklist and final admission return `go: true`.
