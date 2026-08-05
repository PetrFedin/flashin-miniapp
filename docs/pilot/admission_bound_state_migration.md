# Signed admission-bound pilot state migration

This runbook applies when upgrading the controlled first-20-order pilot to state schema v4.

## Safety invariant

One `live_pilot_state.json` belongs to one exact signed `pilot_admission_manifest.json` and is itself HMAC-SHA256 signed. The admission binding includes the manifest SHA-256, creation time, configuration fingerprint, release ID, Git commit and release archive SHA-256. The state signature covers every scenario result, evidence reference, money/stock field, summary and GO/NO-GO/STOP decision.

Do not reuse a pilot state after any admission, signing-secret, configuration or promoted-release change. Do not edit the JSON by hand.

The signing secret is the trust boundary for admission and pilot-state evidence. Keep it outside Git, restrict production read access to the deployment/runtime operators that need it, rotate it after suspected disclosure, and treat rotation as a new pilot admission that requires a fresh schema v4 state.

## Before initialization

1. Confirm that the production `.env` contains the intended provider and pilot settings plus the protected `PILOT_EVIDENCE_SIGNING_SECRET`.
2. Confirm that current and previous release pointers are different and both expose signed pilot capability v12.
3. Generate fresh provider, live-gate and rollback evidence.
4. Create the signed admission manifest with named owners and all required acknowledgements.
5. Run `make pilot-admission-status`; continue only when it returns GO with no errors.

## Existing schema v1, v2 or v3 state

Schema v1 is admission-unbound. Schema v2 is admission-bound but unsigned. Schema v3 is signed but has no database-anchored replay lineage. All three are intentionally rejected and are never migrated in place.

1. Stop the pilot runtime.
2. Copy `docs/pilot/live_pilot_state.json` and `docs/pilot/live_pilot_summary.md` to an access-controlled evidence archive with a timestamp.
3. Record the archive location and SHA-256 in the change log or incident record.
4. Remove the active legacy state only after the archive has been verified.
5. Create a fresh replay-resistant schema v4 state with `make pilot-init`.
6. Run `make pilot-status` and confirm there is no signature, lineage or admission-binding error.

Never edit the schema number manually and never copy scenario results into a state bound to another admission.

## Normal operator commands

```bash
make pilot-init
make pilot-record ARGS='--number 1 --result running --evidence <reference>'
make pilot-status
make pilot-final
```

Every target revalidates the signed admission and verifies the current state signature. Authorized record changes reread and verify the exact parent file, append its SHA-256, increment the revision and write a new signature. A second writer holding a stale parent is rejected instead of creating a competing signed branch. Status and final validation are read-only, so they cannot inflate or fork the lineage. Direct calls that bypass `scripts/pilot_runner.py` are not part of the supported procedure.

## Expected fail-closed conditions

Stop and investigate when any command reports:

- pilot control state signature invalid;
- state revision rollback or ancestry mismatch;
- concurrent parent-state replacement;
- admission manifest checksum mismatch;
- configuration fingerprint mismatch;
- release ID, Git commit or archive SHA mismatch;
- expired provider, live-gate or admission evidence;
- legacy schema v1, unsigned schema v2 or replay-vulnerable schema v3 state;
- missing or malformed admission binding;
- pilot decision `STOP`.

Do not use `--force` to suppress a mismatch. It is only for an intentional reset after the old evidence has been archived and the reset has an accountable owner.

## Runtime arm and checkout

After initialization, arm the allowlist with `make pilot-runtime-arm ARGS='--telegram-id <id>'`. Runtime arm stores the current state revision and SHA-256 in PostgreSQL. Every checkout independently verifies the HMAC, admission binding and append-only ancestry against that database anchor. The same state or a signed descendant is accepted; the anchor advances inside the same database transaction used by checkout, so a failed transaction does not persist a newer trust point. An older revision or unrelated signed branch keeps checkout closed.

## Release, configuration or signing-secret change during the pilot

1. Stop runtime immediately.
2. Archive the current state and summary.
3. Generate fresh evidence for the new release/configuration.
4. Create a new signed admission.
5. Initialize a fresh replay-resistant schema v4 state.
6. Re-arm runtime only after admission and state verification pass.

Scenario results from the previous admission remain evidence for that run; they do not count toward the new run.
