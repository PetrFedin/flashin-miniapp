# Signed admission-bound pilot state migration

This runbook applies when upgrading the controlled first-20-order pilot to state schema v6.

## Safety invariant

One `live_pilot_state.json` belongs to one exact signed `pilot_admission_manifest.json` and is itself HMAC-SHA256 signed. The admission binding includes the manifest SHA-256, creation time, configuration fingerprint, release ID, Git commit and release archive SHA-256. The state signature covers every scenario result, evidence reference, money/stock field, summary and GO/NO-GO/STOP decision.

Do not reuse a pilot state after any admission, signing-secret, configuration or promoted-release change. Do not edit the JSON by hand.

The signing secret is the trust boundary for admission and pilot-state evidence. Keep it outside Git, restrict production read access to the deployment/runtime operators that need it, rotate it after suspected disclosure, and treat rotation as a new pilot admission that requires a fresh schema v5 state.

## Before initialization

1. Confirm that the production `.env` contains the intended provider and pilot settings plus the protected `PILOT_EVIDENCE_SIGNING_SECRET`.
2. Confirm that current and previous release pointers are different and both expose the same signed pilot capability v16. Mixed capability versions fail closed and require a fresh release promotion before admission.
3. Verify the release in both supported Python modes: backend services import the audit and lineage modules through the `scripts.*` package, while operator commands execute them directly from the `scripts` directory. Both paths must load the same capability v15 code and pass before deployment.
4. Keep `live_pilot_state.json`, its `.lock` file and `live_pilot_summary.md` on the same POSIX filesystem mounted read-write for operator commands. The filesystem must provide working advisory `flock` semantics; object storage and unverified network filesystems are not supported.
5. Generate fresh provider, live-gate and rollback evidence.
6. Create the signed admission manifest with named owners and all required acknowledgements. Each owner value must identify one accountable individual. Team names, role aliases, shared accounts and generic values such as `Operations` are not acceptable production identities. If an owner changes, stop runtime and create a fresh signed admission before that person records another mutation.
7. Run `make pilot-admission-status`; continue only when it returns GO with no errors.

## Existing schema v1, v2, v3, v4 or v5 state

Schema v1 is admission-unbound. Schema v2 is admission-bound but unsigned. Schema v3 is signed but has no database-anchored replay lineage. Schema v4 has replay protection but no admission-owner audit. Schema v5 has accountable signed mutations but does not prove recorded IDs against PostgreSQL. All five are intentionally rejected and are never migrated in place.

1. Stop the pilot runtime.
2. Copy `docs/pilot/live_pilot_state.json` and `docs/pilot/live_pilot_summary.md` to an access-controlled evidence archive with a timestamp.
3. Record the archive location and SHA-256 in the change log or incident record.
4. Remove the active legacy state only after the archive has been verified.
5. Create a fresh database-bound schema v6 state with `make pilot-init`.
6. Run `make pilot-status` and confirm there is no signature, lineage, audit-owner or admission-binding error.

Never edit the schema number manually and never copy scenario results into a state bound to another admission.

## Normal operator commands

```bash
make pilot-init ARGS='--operator-role operations_owner --operator "<signed admission owner>" --reason "Initialize controlled pilot"'
make pilot-record ARGS='--number 1 --result running --operator-role operations_owner --operator "<signed admission owner>" --reason "Verified scenario 1" --evidence <reference>'
make pilot-status
make pilot-final
```

Every target revalidates the signed admission and verifies the current state signature. Every init or record mutation must name an operator role and exact operator name matching that role in the signed admission, plus a durable reason. The signed audit log records a UUID, revision, parent state hash, timestamp, role, owner name, scenario and result; misleading scenario metadata or protected top-level changes are rejected. Authorized record changes hold a cross-process exclusive lock while they reread and verify the exact parent file, append its SHA-256 and increment the revision. The signed JSON state is replaced first with file and parent-directory fsync; the Markdown summary is then regenerated as a derived, non-authoritative view with the exact state revision and SHA-256. A second writer waits for the lock and is then rejected as stale instead of creating or overwriting a competing signed branch. Lock acquisition has a bounded timeout and fails closed. If a process or host stops after the authoritative state commit but before the derived summary commit, the next `pilot-status` or `pilot-final` validates the signed state and repairs the summary without advancing the revision. Status and final validation are read-only, so they cannot inflate or fork the lineage. Direct calls that bypass `scripts/pilot_runner.py` are not part of the supported procedure.

After any operator write reports a filesystem or summary error, do not repeat the same `pilot-record` command blindly. First run `make pilot-status`: if the signed JSON revision already advanced, treat that state as committed and use the regenerated summary; if state validation fails or the revision did not advance, stop the pilot and investigate the filesystem before another mutation.

## Expected fail-closed conditions

Stop and investigate when any command reports:

- pilot control state signature invalid;
- state revision rollback or ancestry mismatch;
- concurrent parent-state replacement;
- pilot state lock acquisition timeout;
- durable file or parent-directory fsync failure;
- admission manifest checksum mismatch;
- configuration fingerprint mismatch;
- release ID, Git commit or archive SHA mismatch;
- expired provider, live-gate or admission evidence;
- legacy schema v1, unsigned schema v2, replay-vulnerable schema v3 or unattributed schema v4 or database-unverified schema v5 state;
- mutation operator not matching the signed admission owner;
- mutation reason, scenario or result not matching the actual state change;
- missing or malformed admission binding;
- pilot decision `STOP`.

Do not use `--force` to suppress a mismatch. It is only for an intentional reset after the old evidence has been archived and the reset has an accountable owner.

## Runtime arm and checkout

After initialization, arm the allowlist with `make pilot-runtime-arm ARGS='--telegram-id <id>'`. Runtime arm stores the current state revision and SHA-256 in PostgreSQL. Every checkout independently verifies the HMAC, admission binding, accountable audit owners and append-only ancestry against that database anchor. The same state or a signed descendant is accepted; the anchor advances inside the same database transaction used by checkout, so a failed transaction does not persist a newer trust point. An older revision, unrelated signed branch or mutation attributed to a person outside the current signed admission keeps checkout closed.

## Release, configuration, owner or signing-secret change during the pilot

1. Stop runtime immediately.
2. Archive the current state and summary.
3. Generate fresh evidence for the new release, configuration or owner set.
4. Create a new signed admission.
5. Initialize a fresh database-bound schema v6 state.
6. Re-arm runtime only after admission and state verification pass.

Scenario results from the previous admission remain evidence for that run; they do not count toward the new run.


## Database-bound scenario evidence

A scenario may be recorded as `pass` only after its exact sequence has a
PostgreSQL `pilot_order_slots` row. The signed `order_id` must equal that
slot's order. Provider payment and refund identifiers must resolve to exactly
one row owned by the same order, while recorded order, payment and return
statuses, amounts and currencies must match PostgreSQL. Final GO additionally
requires runtime status `completed`, exactly 20 accepted orders and an exact
sequence-by-sequence match between all 20 scenarios and slots. Database
unavailability or any mismatch fails closed.
