# FLASHIN pilot launch preflight

`make pilot-launch-preflight` is the final read-only orchestration check before the controlled pilot runtime may be armed.

It is intentionally **not** a deployment command, provider probe, payment runner, evidence generator, evidence attachment command, or pilot arm command. A successful result means only: the deployed environment is ready for the explicit `make pilot-runtime-arm` step.

## Where to run it

Run the command from the exact deployed production checkout after the immutable current release has been promoted. The checkout must be clean and must match the retained release archive.

The preflight reuses the production deploy provenance gate, so the current release commit must still be the current protected `main` head and must have a completed successful exact-SHA `push` CI run with every required job green.

```bash
make pilot-launch-preflight
```

The command prints one JSON report and exits non-zero until every launch stage is complete.

For local diagnostics only:

```bash
make pilot-launch-preflight ARGS='--local-only'
```

`--local-only` deliberately skips the GitHub read and therefore can **never** return `go=true`. Never use local-only output as launch evidence.

## Stage order

The report evaluates these stages in a fixed order:

1. `release_pointer` — current release pointer resolves to a valid immutable archive.
2. `repository_provenance` — the release SHA is current protected `main` and has exact successful `push` CI.
3. `release_checkout` — the deployed checkout is clean and byte-bound to the retained release archive.
4. `baseline_admission` — the signed baseline pilot admission remains valid for the current/previous release pair and evidence windows.
5. `real_order_context` — controlled YooKassa order context is either ready to be created or is durably at `payment_created`; any interrupted provisional phase blocks continuation.
6. `live_lifecycle_evidence` — the signed same-order lifecycle attachment is valid.
7. `repository_governance_evidence` — the signed governance attachment is valid for the exact release.
8. `launch_checklist` — the signed P01-P20 checklist attachment is valid.
9. `final_admission` — the complete admission chain passes the same validator used by runtime arm.

The first stage that is not `complete` becomes the top-level `phase`, and its `next_action` is the only recommended continuation.

## Status meanings

- `complete` — the stage is verified now.
- `ready` — no conflicting state exists, but an explicit side-effectful operator step is still required. For example, a missing real-order context is `ready`, not `complete`.
- `blocked` — continuing would violate a launch invariant or requires reconciliation.

Top-level `go=true` is possible only when **every** stage is `complete`. It means `ready_for_pilot_runtime_arm`, not that the pilot has been armed or opened to customers.

## Interrupted real-order runs

The real-order context is crash-safe and exclusive. If its phase is `preflight_intent`, `checkout_intent`, or `order_created`, the preflight returns `blocked` and must not recommend another payment attempt.

Use:

```bash
make real-order-e2e-status
```

Then follow [`real_provider_e2e_recovery.md`](./real_provider_e2e_recovery.md). Do not delete or replace the private context merely to make preflight green.

Only a structurally valid `payment_created` context is considered complete at this stage. It still does not prove provider settlement, fulfillment, refund, MoySklad outbound operations, Telegram notification delivery, or signed lifecycle admission; those are separate later stages.

## Secrets and evidence

The preflight does not persist a report and is not itself launch evidence. Existing signed reports remain authoritative.

Configured token/secret/password/API-key values are redacted from surfaced errors, including a process-only `FLASHIN_GITHUB_TOKEN` or `GITHUB_TOKEN`. Do not pipe raw environment dumps into the report or commit console output containing operator credentials.

## Required final transition

When the report returns:

```json
{
  "go": true,
  "meaning": "ready_for_pilot_runtime_arm",
  "phase": "runtime_arm",
  "next_action": "make pilot-runtime-arm"
}
```

run the runtime arm only with the explicit controlled Telegram allowlist required by the pilot runbook.

`make pilot-runtime-arm` executes the read-only launch preflight first, then re-verifies final admission, then calls the host runtime arm. The host `pilot_runtime.py arm` independently runs the launch preflight again before any Docker/database arm mutation. This second fresh check prevents bypass by invoking the Python command directly and narrows the race window if GitHub governance or exact-main provenance changes between operator checks and the actual arm transition.
