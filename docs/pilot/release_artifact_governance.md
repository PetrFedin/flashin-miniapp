# Immutable release artifact gate

The manual GitHub `Release` workflow is an artifact-export boundary, not a deployment bypass. It must fail closed unless the exact source revision is already trusted as the current protected `main` release candidate.

## Required source state

`scripts/release_ci_gate.py` requires all of the following before packaging:

- the workflow was dispatched from `main`;
- the requested SHA is the current `main` head;
- GitHub reports `main` as protected;
- an exact-SHA **push** run of `.github/workflows/ci.yml` completed successfully on `main`;
- that exact CI run contains successful `backend`, `frontend`, `admin`, `browser-e2e`, `integrated-e2e`, and `docker` jobs.

A successful pull-request run is deliberately insufficient for release export. The protected-branch push CI is the artifact trust boundary.

## Artifact construction

The workflow does not recursively ZIP the runner workspace. It uses `scripts/release_control.py create`, which packages only allowed tracked files and embeds a deterministic `release_manifest.json` with per-file hashes, sizes and modes. The generated ZIP is immediately verified by `release_control.py verify` and inspected by `pilot_release_capability.py inspect` before upload.

The workflow uploads only:

- the immutable release ZIP;
- its SHA-256 checksum file.

No `.env`, live pilot evidence, runtime release pointers, backups, media, local databases, secrets or generated Alertmanager receiver configuration are permitted in the release archive.

## Operational boundary

This workflow does **not** deploy production, arm the pilot, merge PR #130, or prove live providers. A generated artifact remains unusable for real-money pilot admission until repository governance, live provider evidence, deployed lifecycle evidence, rollback evidence and the signed launch checklist are all valid for the exact promoted release.

Because `main` protection is itself mandatory, the workflow intentionally refuses to create a release artifact while GitHub reports `main` as unprotected.
