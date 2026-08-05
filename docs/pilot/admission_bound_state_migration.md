# Admission-bound pilot state migration

This runbook applies when upgrading the controlled first-20-order pilot to state schema v2.

## Safety invariant

One `live_pilot_state.json` belongs to one exact signed `pilot_admission_manifest.json`. The binding includes the manifest SHA-256, admission creation time, configuration fingerprint, release ID, Git commit and release archive SHA-256.

Do not reuse a pilot state after any admission, configuration or promoted-release change.

## Before initialization

1. Confirm that the production `.env` contains the intended provider and pilot settings.
2. Confirm that current and previous release pointers are different and both expose signed pilot capability v10.
3. Generate fresh provider, live-gate and rollback evidence.
4. Create the signed admission manifest with named owners and all required acknowledgements.
5. Run `make pilot-admission-status`; continue only when it returns GO with no errors.

## Existing schema v1 state

Schema v1 is intentionally rejected and is never migrated in place.

1. Stop the pilot runtime.
2. Copy `docs/pilot/live_pilot_state.json` and `docs/pilot/live_pilot_summary.md` to an access-controlled evidence archive with a timestamp.
3. Record the archive location in the change log or incident record.
4. Remove the active schema v1 state only after the archive has been verified.
5. Create a new state with `make pilot-init`.

Never edit the schema number manually and never copy scenario results into a state bound to another admission.

## Normal operator commands

All control operations must use the admission-gated Make targets:

```bash
make pilot-init
make pilot-record ARGS='--number 1 --result running --evidence <reference>'
make pilot-status
make pilot-final
```

The targets revalidate the signed admission before reading or changing the state. Direct calls that bypass `scripts/pilot_runner.py` are not part of the supported pilot procedure.

## Expected fail-closed conditions

Stop and investigate when any command reports:

- admission manifest checksum mismatch;
- configuration fingerprint mismatch;
- release ID, Git commit or archive SHA mismatch;
- expired provider, live-gate or admission evidence;
- legacy schema v1 state;
- missing or malformed admission binding;
- pilot decision `STOP`.

Do not use `--force` to suppress a mismatch. `--force` is only for an intentional reset after the old evidence has been archived and the reset has an accountable owner.

## Runtime arm and checkout

After the new state is created, arm the allowlist with `make pilot-runtime-arm ARGS='--telegram-id <id>'`. Runtime arm and every checkout independently compare the active state with the current signed admission. A mismatch keeps checkout closed.

## Release or configuration change during the pilot

1. Stop runtime immediately.
2. Archive the current state and summary.
3. Generate fresh evidence for the new release/configuration.
4. Create a new signed admission.
5. Initialize a fresh schema v2 state.
6. Re-arm the runtime only after admission and state verification pass.

Scenario results from the previous admission remain evidence for that run; they do not count toward the new run.
