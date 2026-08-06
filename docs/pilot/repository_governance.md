# FLASHIN repository governance gate

Repository governance is a mandatory pilot input. A green CI run is insufficient when `main` can still be changed by a direct push, force-push, deletion, hidden ruleset bypass, spoofed status source, or an unaudited administrator action after the tested commit was produced.

The v19 gate creates a signed, short-lived report bound to:

- `PetrFedin/flashin-miniapp`;
- the configured default branch (`main`);
- the exact `git_commit` in `deploy/release/runtime/current_release.json`;
- the exact successful `CI` workflow run for that commit;
- required checks `backend,frontend,admin,browser-e2e,docker`;
- the official GitHub Actions App ID `15368` as the required source of every check;
- the exact governance-capable release archive, including the protected CI workflow itself;
- the production configuration fingerprint;
- the named technical owner in the signed pilot admission.

## Required GitHub policy

Configure a branch ruleset or classic branch protection for `main` with all of the following:

1. Changes require a pull request.
2. Required status checks include `backend`, `frontend`, `admin`, `browser-e2e`, and `docker`.
3. Every required check explicitly selects **GitHub Actions** as its expected source; `any source`, missing `app_id`/`integration_id`, `-1`, or another App ID is forbidden.
4. Strict status checks are enabled so the branch must be current before merge.
5. Force-push is explicitly forbidden.
6. Branch deletion is explicitly forbidden.
7. Administrator enforcement is enabled for classic protection, or every active ruleset exposes an empty `bypass_actors` list.
8. The branch remains the repository default branch.
9. The successful workflow is the tracked `.github/workflows/ci.yml` capability contained in the exact promoted release.

The evidence command fails closed when any item is absent, when a protection property is omitted, when a required check comes from an untrusted source, when bypass information is hidden, when the remote branch head differs from the promoted release commit, or when no successful completed workflow exists for that exact commit.

## Privileged operator token

`PILOT_GITHUB_TOKEN` is mandatory only while the governance report is being created. Use a dedicated fine-grained personal access token or GitHub App installation token for this repository. For a fine-grained token, grant:

- **Actions: read** — to read the successful workflow run for the exact release commit;
- **Administration: write** — to read branch protection and to make GitHub return the complete `bypass_actors` property for rulesets;
- repository metadata access, which GitHub includes for fine-grained repository tokens.

GitHub intentionally omits `bypass_actors` when the caller does not have write access to the ruleset. The gate treats an omitted property as **NO-GO**, never as an empty bypass list.

The token is an operator credential, not an application credential. **Never place it in the repository root `.env`, `.env.production`, Compose environment, container secrets, application database, pilot evidence, logs or screenshots.** The project Compose file passes root `.env` to backend, frontend, admin, bot and worker containers, so storing the token there would unnecessarily expose repository-administration access to application services.

Use the narrowest repository scope: only `PetrFedin/flashin-miniapp`. Inject the token from an operator secret manager only into the single `pilot-governance-create` process, then remove it from the process environment. Rotate/revoke it after the pilot if it is not required for continuing operations.

## Required check source

Set `PILOT_GITHUB_ACTIONS_APP_ID=15368`, the official GitHub Actions App ID. The collector reads:

- classic protection `required_status_checks.checks[].app_id`;
- ruleset `required_status_checks[].integration_id`.

Names in legacy `contexts` remain visible for diagnostics but do not satisfy source binding by themselves. This prevents a user, webhook integration or another GitHub App with repository write access from spoofing a successful `backend`, `frontend`, `admin`, `browser-e2e`, or `docker` status.

## Non-secret application configuration

Keep only these non-secret values in the production `.env`:

```dotenv
PILOT_GITHUB_REPOSITORY=PetrFedin/flashin-miniapp
PILOT_GITHUB_PROTECTED_BRANCH=main
PILOT_GITHUB_REQUIRED_CHECKS=backend,frontend,admin,browser-e2e,docker
PILOT_GITHUB_ACTIONS_APP_ID=15368
PILOT_GITHUB_WORKFLOW_NAME=CI
PILOT_GITHUB_WORKFLOW_PATH=ci.yml
PILOT_GITHUB_GOVERNANCE_MAX_AGE_MINUTES=60
```

Do not add a `PILOT_GITHUB_TOKEN` line to this file, even with an empty placeholder.

## Execution order

Governance evidence is created only after the exact immutable release is promoted and its GitHub Actions workflow has completed successfully.

```bash
make release-status
# The operator secret manager injects PILOT_GITHUB_TOKEN only for this process.
PILOT_GITHUB_TOKEN="$TOKEN_FROM_OPERATOR_SECRET_MANAGER" \
  make pilot-governance-create ARGS='--owner "Exact technical owner name"'
unset PILOT_GITHUB_TOKEN
make pilot-governance-status
```

Avoid copying the raw token into shell history. Prefer the secret manager's process-injection command, an ephemeral environment wrapper, or a short-lived GitHub App installation token. The example above names the environment boundary; it is not a recommendation to paste a token into an interactive command line.

The owner must exactly match `technical_owner` in the signed admission. Attach governance only after the live lifecycle report has already been attached:

```bash
make pilot-lifecycle-status
make pilot-governance-attach
make pilot-admission-status
```

`pilot-admission-status` is the final verifier and checks baseline admission evidence, live lifecycle evidence, repository governance evidence, release capability, signatures, checksums, age windows, owner identity, exact release commit, complete bypass visibility, trusted check sources, and successful required CI.

## Runtime binding

When the controlled pilot is initialized or armed, the runtime state stores the SHA-256 of the governance report in its immutable admission binding. Replacing the report, changing the admission, promoting another release, changing the production configuration, hiding ruleset bypass data, changing a required check source, or allowing the report to expire causes runtime validation to fail closed. A fresh signed admission and a fresh pilot state are then required.

The report contains policy metadata, release identity and workflow IDs; it never contains the GitHub token. Verification of an already signed report does not require the token.

## Evidence handling

The generated JSON and Markdown files are local/private and ignored by Git:

- `docs/pilot/repository_governance_report.json`
- `docs/pilot/repository_governance_report.md`

Do not place GitHub tokens, provider secrets, raw Telegram initData, cookies, authorization headers, or private customer identifiers in these files.
