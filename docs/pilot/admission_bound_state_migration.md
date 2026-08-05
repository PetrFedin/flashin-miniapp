# Signed admission-bound pilot state migration

This runbook applies when upgrading the controlled first-20-order pilot to state schema v3.

## Safety invariant

One `live_pilot_state.json` belongs to one exact signed `pilot_admission_manifest.json` and is itself HMAC-SHA256 signed. The admission binding includes the manifest SHA-256, creation time, configuration fingerprint, release ID, Git commit and release archive SHA-256. The state signature covers every scenario result, evidence reference, money/stock field, summary and GO/NO-GO/STOP decision.

Do not reuse a pilot state after any admission, signing-secret, configuration or promoted-release change. Do not edit the JSON by hand.

## Before initialization

1. Confirm that the production `.env` contains the intended provider and pilot settings plus the protected `PILOT_EVIDENCE_SIGNING_SECRET`.
2. Confirm that current and previous release pointers are different and both expose signed pilot capability v11.
3. Generate fresh provider, live-gate and rollback evidence.
4. Create the signed admission manifest with named owners and all required acknowledgements.
5. Run `make pilot-admission-status`; continue only when it returns GO with no errors.

## Existing schema v1 or v2 state

Schema v1 is admission-unbound. Schema v2 is admission-bound but unsigned. Both are intentionally rejected and are never migrated in place.

1. Stop the pilot runtime.
2. Copy `docs/pilot/live_pilot_state.json` and `docs/pilot/live_pilot_summary.md` to an access-controlled evidence archive with a timestamp.
3. Record the archive location and SHA-256 in the change log or incident record.
4. Remove the active legacy state only after the archive has been verified.
5. Create a fresh signed schema v3 state with `make pilot-init`.
6. Run `make pilot-status` and confirm there is no signature or admission-binding error.

Never edit the schema number manually and never copy scenario results into a state bound to another admission.

## Normal operator commands

```bash
make pilot-init
make pilot-record ARGS='--number 1 --result running --evidence <reference>'
make pilot-status
make pilot-final
```

Every target revalidates the signed admission, verifies the current state signature before reading it, and writes a new signature after an authorized state change. Direct calls that bypass `scripts/pilot_runner.py` are not part of the supported procedure.

## Expected fail-closed conditions

Stop and investigate when any command reports:

- pilot control state signature invalid;
- admission manifest checksum mismatch;
- configuration fingerprint mismatch;
- release ID, Git commit or archive SHA mismatch;
- expired provider, live-gate or admission evidence;
- legacy schema v1 or unsigned schema v2 state;
- missing or malformed admission binding;
- pilot decision `STOP`.

Do not use `--force` to suppress a mismatch. It is only for an intentional reset after the old evidence has been archived and the reset has an accountable owner.

## Runtime arm and checkout

After initialization, arm the allowlist with `make pilot-runtime-arm ARGS='--telegram-id <id>'`. Runtime arm and every checkout independently verify the state HMAC and compare the state with the exact signed admission. A signature or binding mismatch keeps checkout closed.

## Release, configuration or signing-secret change during the pilot

1. Stop runtime immediately.
2. Archive the current state and summary.
3. Generate fresh evidence for the new release/configuration.
4. Create a new signed admission.
5. Initialize a fresh signed schema v3 state.
6. Re-arm runtime only after admission and state verification pass.

Scenario results from the previous admission remain evidence for that run; they do not count toward the new run.
