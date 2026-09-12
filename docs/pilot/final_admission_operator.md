# FLASHIN final pilot admission — operator gate

This is the final fail-closed boundary before the controlled first-20 runtime may be armed.

## Required chain

Do not treat lifecycle-only or governance-only verification as pilot approval. The final admission is valid only when the same signed admission manifest contains and validates all three attached layers for the exact current release and production configuration:

1. deployed live lifecycle evidence;
2. protected-repository governance evidence;
3. signed P01-P20 launch-checklist evidence.

The P01-P20 report is generated from `docs/pilot/live_pilot_runner.json`. Every critical step must be `pass`. Optional steps may be `pass` or explicit `skip` with a meaningful reason. PASS evidence must remain sanitized and checksum-bound under `docs/pilot/evidence`; raw Telegram `initData`, provider credentials, authorization headers and customer secrets are forbidden.

## Operator sequence

After live lifecycle and repository governance are attached to the signed admission, complete the P01-P20 source and run:

```bash
make pilot-checklist-create
make pilot-checklist-status
make pilot-checklist-attach
make pilot-admission-status
```

`make pilot-admission-status` intentionally calls `scripts/pilot_launch_admission.py verify`. It is the final verifier and must fail if lifecycle, governance or launch-checklist evidence is missing, stale, invalid, mismatched to the exact release/configuration, signed by the wrong chain, or owned by names outside the signed admission.

Do not replace this command with `make pilot-governance-status`; governance status is an intermediate gate only.

## Runtime arm

Only after `make pilot-admission-status` returns `go: true` may the operator arm the explicit Telegram allowlist:

```bash
make pilot-runtime-arm ARGS='--telegram-id 123456789 --telegram-id 987654321'
```

The Make target deliberately runs the final admission verifier again immediately before invoking `pilot_runtime.py arm`. Telegram IDs are passed only to the runtime command, not to the admission verifier. If final admission changes, expires, loses an attachment, or no longer matches the current release, arm fails before runtime mutation.

The runtime remains capped at exactly 20 accepted orders and must be stopped immediately on any money, inventory, evidence, release, reconciliation, worker-health or queue-integrity failure.
