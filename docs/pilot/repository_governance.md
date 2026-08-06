# FLASHIN repository governance gate

Repository governance is a mandatory pilot input. A green CI run is insufficient when `main` can still be changed by a direct push, force-push, deletion, hidden ruleset bypass, or an unaudited administrator action after the tested commit was produced.

The v19 gate creates a signed, short-lived report bound to:

- `PetrFedin/flashin-miniapp`;
- the configured default branch (`main`);
- the exact `git_commit` in `deploy/release/runtime/current_release.json`;
- the exact successful `CI` workflow run for that commit;
- required checks `backend,frontend,admin,browser-e2e,docker`;
- the exact governance-capable release archive, including the protected CI workflow itself;
- the production configuration fingerprint;
- the named technical owner in the signed pilot admission.

## Required GitHub policy

Configure a branch ruleset or classic branch protection for `main` with all of the following:

1. Changes require a pull request.
2. Required status checks include `backend`, `frontend`, `admin`, `browser-e2e`, and `docker`.
3. Strict status checks are enabled so the branch must be current before merge.
4. Force-push is forbidden.
5. Branch deletion is forbidden.
6. Administrator enforcement is enabled for classic protection, or every active ruleset exposes an empty `bypass_actors` list.
7. The branch remains the repository default branch.
8. The successful workflow is the tracked `.github/workflows/ci.yml` capability contained in the exact promoted release.

The evidence command fails closed when any item is absent, when bypass information is hidden, when the remote branch head differs from the promoted release commit, or when no successful completed workflow exists for that exact commit.

## GitHub token

`PILOT_GITHUB_TOKEN` is mandatory. Use a dedicated fine-grained personal access token or GitHub App installation token for this repository. For a fine-grained token, grant:

- **Actions: read** — to read the successful workflow run for the exact release commit;
- **Administration: write** — to read branch protection and to make GitHub return the complete `bypass_actors` property for rulesets;
- repository metadata access, which GitHub includes for fine-grained repository tokens.

GitHub intentionally omits `bypass_actors` when the caller does not have write access to the ruleset. The gate treats an omitted property as **NO-GO**, never as an empty bypass list.

Use the narrowest repository scope: only `PetrFedin/flashin-miniapp`. Do not grant access to unrelated repositories. Store the token only in the production secret store or host `.env`. Never add it to source control, logs, screenshots, or pilot evidence. Rotate/revoke it after the pilot if it is not required for continuing operations.

## Environment

Set these values outside Git:

```dotenv
PILOT_GITHUB_TOKEN=
PILOT_GITHUB_REPOSITORY=PetrFedin/flashin-miniapp
PILOT_GITHUB_PROTECTED_BRANCH=main
PILOT_GITHUB_REQUIRED_CHECKS=backend,frontend,admin,browser-e2e,docker
PILOT_GITHUB_WORKFLOW_NAME=CI
PILOT_GITHUB_WORKFLOW_PATH=ci.yml
PILOT_GITHUB_GOVERNANCE_MAX_AGE_MINUTES=60
```

## Execution order

Governance evidence is created only after the exact immutable release is promoted and its GitHub Actions workflow has completed successfully.

```bash
make release-status
make pilot-governance-create ARGS='--owner "Exact technical owner name"'
make pilot-governance-status
```

The owner must exactly match `technical_owner` in the signed admission. Attach governance only after the live lifecycle report has already been attached:

```bash
make pilot-lifecycle-status
make pilot-governance-attach
make pilot-admission-status
```

`pilot-admission-status` is the final verifier and checks baseline admission evidence, live lifecycle evidence, repository governance evidence, release capability, signatures, checksums, age windows, owner identity, exact release commit, complete bypass visibility, and successful required CI.

## Runtime binding

When the controlled pilot is initialized or armed, the runtime state stores the SHA-256 of the governance report in its immutable admission binding. Replacing the report, changing the admission, promoting another release, changing the production configuration, hiding ruleset bypass data, or allowing the report to expire causes runtime validation to fail closed. A fresh signed admission and a fresh pilot state are then required.

## Evidence handling

The generated JSON and Markdown files are local/private and ignored by Git:

- `docs/pilot/repository_governance_report.json`
- `docs/pilot/repository_governance_report.md`

Do not place GitHub tokens, provider secrets, raw Telegram initData, cookies, authorization headers, or private customer identifiers in these files.
