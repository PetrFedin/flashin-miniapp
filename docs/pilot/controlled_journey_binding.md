# Controlled pilot journey binding — v29

This gate prevents individually valid pilot evidence from different sessions or orders being combined into one runtime admission.

It is deliberately downstream of the existing signed gates. It does **not** replace Telegram, YooKassa, webhook, order, stock/MoySklad, fulfillment, refund, notification, repository-governance, rollback, provider-readiness, live-lifecycle, or P01–P20 evidence.

## Security model

A controlled pilot journey uses one fresh opaque UUIDv4. The UUID is not a Telegram user ID, order ID, YooKassa payment ID, MoySklad ID, email, phone number, or other customer/provider identifier.

`scripts/pilot_journey_binding.py` creates a small signed anchor under `docs/pilot/evidence/controlled_journey_anchor.json`. The anchor is bound to:

- the exact immutable release;
- the current production configuration fingerprint;
- a short freshness window;
- one random UUIDv4;
- the pilot evidence signing secret through HMAC-SHA256.

The exact anchor file and SHA-256 must then be present as an evidence item in **both**:

1. `docs/pilot/live_lifecycle_report.json`;
2. `docs/pilot/launch_checklist_report.json`.

After the final pilot admission has attached those two exact reports, v29 creates `docs/pilot/journey_binding_report.json`. The signed binding fixes the exact SHA-256 of:

- the controlled journey anchor;
- the live lifecycle report;
- the P01–P20 launch checklist report;
- the final pilot admission manifest.

The binding verifier revalidates the underlying lifecycle, checklist and final admission rather than trusting only their file hashes. It also verifies that the final admission itself references the same lifecycle/checklist hashes and that both evidence reports reference the exact same anchor hash.

`pilot_runner.py` now checks this binding after the normal admission and final launch admission. A controlled 20-order pilot operation cannot start when the binding is missing, stale, invalid, mismatched or points to a different admission/report/anchor.

## Operator flow

Generate a fresh anchor on the exact deployed release before collecting the controlled journey evidence:

```bash
python scripts/pilot_journey_binding.py init
```

Do not reuse the anchor for another customer journey or another release. `init` refuses to overwrite an existing anchor unless `--force` is explicitly supplied.

When collecting live lifecycle evidence, include the anchor as one sanitized evidence entry in at least one required lifecycle scenario, for example:

```json
{
  "label": "controlled journey anchor",
  "path": "docs/pilot/evidence/controlled_journey_anchor.json"
}
```

When completing P01–P20 for the same controlled journey, include the same anchor file as one evidence entry in at least one passing checklist step. The lifecycle/checklist signers calculate and persist its SHA-256, so the later gate does not rely on a filename alone.

Then create and verify the normal artifacts in their existing order:

```bash
python scripts/pilot_live_lifecycle.py create
python scripts/pilot_live_lifecycle.py verify
python scripts/pilot_launch_checklist.py create
python scripts/pilot_launch_checklist.py verify
python scripts/pilot_launch_admission.py attach
python scripts/pilot_launch_admission.py verify
```

Finally create the cross-artifact binding:

```bash
python scripts/pilot_journey_binding.py create
python scripts/pilot_journey_binding.py verify
```

Only after all gates are green should the admission-gated runtime wrapper be used:

```bash
python scripts/pilot_runner.py status
```

The same wrapper must be used for pilot start/stop/status operations; it fails closed before delegating to `pilot_control` if any admission or journey-binding check fails.

## Failure cases

The gate returns NO-GO when any of the following occurs:

- the anchor is absent, unsigned, stale, from another release/configuration, or not a canonical non-nil UUIDv4;
- the anchor file is missing, empty, oversized, outside `docs/pilot/evidence`, or a symlink;
- lifecycle and checklist do not both reference the exact anchor path and SHA-256;
- lifecycle/checklist are invalid under their existing signed validators;
- the final admission is invalid;
- the final admission references different lifecycle/checklist hashes;
- any bound artifact changes after `journey_binding_report.json` is signed;
- the binding report is stale, has a bad signature, or is for another release/configuration.

## Evidence boundary

The journey UUID is intentionally opaque and non-sensitive. Do not put customer data, provider identifiers, payment IDs, order IDs, Telegram IDs, raw Mini App `initData`, secrets or provider response bodies into the anchor.

The CI tests validate only this software policy. They do not create live Telegram/YooKassa/MoySklad evidence and must not be used to mark P01–P20 PASS. Real-money pilot status remains NO-GO until deployed evidence and all manual launch gates are complete.
