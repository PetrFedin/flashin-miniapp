# Provider evidence status-only policy — pilot v28

This policy is a fail-closed layer on top of the v26 provider-probe privacy controls and the v27 status-only signer. It does not change the Telegram → YooKassa → webhook → order → stock/MoySklad → fulfillment → refund → notification business lifecycle.

## Rule

Signed provider evidence must contain provider readiness status, not provider output.

The live probe runner may transiently capture bounded/redacted subprocess output while a probe is executing, but `build_report()` discards that output before signing or writing evidence. Each persisted result contains exactly:

- a bounded probe name from the fixed pilot provider set;
- `ok` as a boolean;
- a numeric process exit code or `null`;
- empty `stdout`;
- empty `stderr`.

An unknown probe name is collapsed to `unknown`, and a non-integer return code is collapsed to `null` before signing. Arbitrary result fields are never copied into newly generated evidence.

For a fully passing report, every provider result must have `ok=true` and `returncode=0`.

## Shared verification and admission

v28 moves the persisted-result policy into the shared `scripts/pilot_evidence.py` provider validator used by pilot admission. The shared validator rejects:

- non-object result records;
- unsupported or missing result fields;
- non-empty probe `stdout` or `stderr`;
- a non-boolean `ok` value;
- a return code that is neither an integer nor `null`;
- a passing result whose return code is not zero.

`scripts/check_integrations.py verify` already calls this shared provider validator and retains its v27 status-only guard. `scripts/pilot_admission.py` also consumes the shared validator, so final admission cannot accept a provider report that operational verification would reject merely because that report was re-signed with a valid signing secret.

This means a report from an older persistence format that retained probe output is not reusable for the v28 operational or admission gates. Generate fresh evidence on the exact deployed immutable release instead of editing or migrating old evidence.

## Why this exists

Redaction is useful defense in depth but cannot enumerate every future provider identifier, PII field, transformed credential, URL encoding, proxy message, or third-party error body. Status-only persistence removes that dependency from signed launch evidence. Shared validation also prevents policy drift between the probe-verification command and final pilot admission.

v26 remains the first layer: individual Telegram, YooKassa, MoySklad, R2/S3 and Meilisearch probes should emit bounded safe output. v27 adds the persistence boundary. v28 makes the same boundary authoritative for final admission.

## Regression coverage

The backend tests verify that:

1. unknown provider output and URLs are absent from newly signed reports;
2. unknown result names and non-numeric return codes are bounded before signing;
3. a report modified to reintroduce output and then re-signed with a valid test signing secret is rejected by the shared provider validator;
4. unsupported fields and inconsistent passing return codes are rejected;
5. pilot admission preflight rejects a re-signed provider report containing private output or arbitrary provider-reference fields;
6. clean status-only evidence remains valid for admission.

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

A successful CI run proves only the code path. It does not complete Telegram, YooKassa, MoySklad, fulfillment, refund, notification, public HTTPS, or P01–P20 live evidence. Internal integrated E2E remains an internal-stack test, not live provider evidence. Real-money pilot admission remains NO-GO until the deployed signed checklist and final admission return `go: true`.
