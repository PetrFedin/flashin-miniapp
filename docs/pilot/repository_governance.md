# FLASHIN repository governance gate

Repository governance is a mandatory pilot input. A green core CI run is insufficient when `main` can still be changed by a direct push, force-push, deletion, hidden ruleset bypass, spoofed status source, an unaudited administrator action, or when supply-chain security failed for the release that is about to be admitted.

The governance gate creates a signed, short-lived report bound to:

- `PetrFedin/flashin-miniapp`;
- the default protected branch `main`;
- the exact `git_commit` in `deploy/release/runtime/current_release.json`;
- the exact successful `push` run of `.github/workflows/ci.yml` for that commit;
- required checks `backend,frontend,admin,browser-e2e,integrated-e2e,docker`;
- a signed bounded `required_jobs` verdict proving all six trusted CI jobs completed successfully on that exact run;
- the exact successful `push` run of `.github/workflows/security.yml` for the same commit;
- a signed bounded Security verdict proving dependency review, CodeQL, secret scan, dependency vulnerability scan, and every shipped runtime image/SBOM gate completed successfully;
- the official GitHub Actions App ID `15368` as the required source of every protected-branch core check;
- the exact governance-capable release archive, including the protected CI and Security workflow capabilities;
- the production configuration fingerprint;
- the named technical owner in the signed pilot admission.

## Required GitHub policy

Configure a branch ruleset or classic branch protection for `main` with all of the following:

1. Changes require a pull request.
2. Required status checks include `backend`, `frontend`, `admin`, `browser-e2e`, `integrated-e2e`, and `docker`.
3. Every required check explicitly selects **GitHub Actions** as its expected source; `any source`, missing `app_id`/`integration_id`, `-1`, or another App ID is forbidden.
4. Strict status checks are enabled so the branch must be current before merge.
5. Force-push is explicitly forbidden.
6. Branch deletion is explicitly forbidden.
7. Administrator enforcement is enabled for classic protection, or every active ruleset exposes an empty `bypass_actors` list.
8. The branch remains the repository default branch.
9. GitHub Dependency Graph is enabled for the repository so Dependency Review can execute. Do not mark dependency review `continue-on-error`, skip it, or replace it with a cosmetic warning.
10. The selected CI workflow is tracked `.github/workflows/ci.yml`, its event is `push`, its head SHA equals the current release commit, and all six trusted jobs are completed with `success` before evidence is signed.
11. The selected Security workflow is tracked `.github/workflows/security.yml`, its event is `push`, its head SHA equals the same current release commit, and every immutable Security job is completed with `success` before evidence is signed.
12. For a push, Dependency Review explicitly compares the previous branch SHA to the new release SHA; a missing/zero previous SHA fails closed instead of silently producing a release verdict without a comparison base.

The evidence command fails closed when any item is absent, when a protection property is omitted, when a required check comes from an untrusted source, when bypass information is hidden, when the remote branch head differs from the promoted release commit, when no successful completed `push` CI workflow exists for that exact commit, when no successful completed `push` Security workflow exists for that exact commit, or when any required CI/Security job is missing, failed or cancelled.

This intentionally means that a disabled Dependency Graph makes pilot admission **NO-GO**: Dependency Review cannot become green, therefore the exact-release Security workflow cannot become trusted governance evidence.

## Privileged operator token

`PILOT_GITHUB_TOKEN` is mandatory only while the governance report is being created. Use a dedicated fine-grained personal access token or GitHub App installation token for this repository. The token must have enough access to read Actions runs/jobs and complete branch/ruleset governance data, including bypass visibility. Do not broaden it to unrelated repositories.

GitHub can omit ruleset bypass details when the caller lacks sufficient access. The gate treats an omitted `bypass_actors` property as **NO-GO**, never as an empty bypass list.

The token is an operator credential, not an application credential. **Never place it in the repository root `.env`, `.env.production`, Compose environment, container secrets, application database, pilot evidence, logs or screenshots.** The project Compose file passes root `.env` to application containers, so storing the token there would unnecessarily expose repository-administration access to application services.

Use the narrowest repository scope: only `PetrFedin/flashin-miniapp`. Inject the token from an operator secret manager only into the single `pilot-governance-create` process, then remove it from the process environment. Rotate/revoke it after the pilot if it is not required for continuing operations.

## Required check source

The immutable trust anchor uses `PILOT_GITHUB_ACTIONS_APP_ID=15368`, the official GitHub Actions App ID. The collector reads:

- classic protection `required_status_checks.checks[].app_id`;
- ruleset `required_status_checks[].integration_id`.

Names in legacy `contexts` remain visible for diagnostics but do not satisfy source binding by themselves. This prevents a user, webhook integration or another GitHub App with repository write access from spoofing a successful `backend`, `frontend`, `admin`, `browser-e2e`, `integrated-e2e`, or `docker` status.

Security workflow identity is a separate immutable trust anchor: the signed report accepts only workflow name `Security`, path `.github/workflows/security.yml`, event `push`, exact current release SHA, completed/success status, and the complete immutable Security job set.

## Non-secret application configuration

These values are immutable trust anchors. They may be omitted from the production `.env` and will then use the exact trusted defaults. If they are present, they must match exactly:

```dotenv
PILOT_GITHUB_REPOSITORY=PetrFedin/flashin-miniapp
PILOT_GITHUB_PROTECTED_BRANCH=main
PILOT_GITHUB_REQUIRED_CHECKS=backend,frontend,admin,browser-e2e,integrated-e2e,docker
PILOT_GITHUB_ACTIONS_APP_ID=15368
PILOT_GITHUB_WORKFLOW_NAME=CI
PILOT_GITHUB_WORKFLOW_PATH=ci.yml
PILOT_GITHUB_GOVERNANCE_MAX_AGE_MINUTES=60
```

Do not add a `PILOT_GITHUB_TOKEN` line to this file, even with an empty placeholder.

The Security workflow name/path and Security required-job set are code-level immutable trust anchors rather than mutable `.env` settings. This prevents a production environment variable from weakening or redirecting the supply-chain gate.

## Execution order

Governance evidence is created only after the exact immutable release is promoted and **both** its protected-main GitHub Actions `push` CI workflow and Security workflow have completed successfully.

```bash
make release-status
# Confirm GitHub Dependency Graph is enabled and the exact-main Security run is green.
# The operator secret manager injects PILOT_GITHUB_TOKEN only for this process.
PILOT_GITHUB_TOKEN="$TOKEN_FROM_OPERATOR_SECRET_MANAGER" \
  make pilot-governance-create ARGS='--owner "Exact technical owner name"'
unset PILOT_GITHUB_TOKEN
make pilot-governance-status
```

Avoid copying the raw token into shell history. Prefer the secret manager's process-injection command, an ephemeral environment wrapper, or a short-lived GitHub App installation token. The example above names the environment boundary; it is not a recommendation to paste a token into an interactive command line.

`make pilot-governance-create` intentionally routes through `scripts/pilot_governance_operator.py`. The lower-level `scripts/pilot_repository_governance.py` may build diagnostic/signed structures for tests and verification, but its direct `create` output does **not** contain the operator-bound six-job CI verdict or the exact-release Security verdict and therefore is not admissible for pilot admission.

The owner must exactly match `technical_owner` in the signed admission. Attach governance only after the live lifecycle report has already been attached:

```bash
make pilot-lifecycle-status
make pilot-governance-attach
make pilot-admission-status
```

`pilot-admission-status` is the final verifier and checks baseline admission evidence, live lifecycle evidence, repository governance evidence, release capability, signatures, checksums, age windows, owner identity, exact release commit, complete bypass visibility, trusted check sources, exact protected-main CI push workflow with its signed six-job verdict, and the exact-release Security push workflow with its complete signed Security verdict.

## Runtime binding

When the controlled pilot is initialized or armed, the runtime state stores the SHA-256 of the governance report in its immutable admission binding. Replacing the report, changing the admission, promoting another release, changing the production configuration, hiding ruleset bypass data, changing a required check source, losing a required CI/Security job verdict, or allowing the report to expire causes runtime validation to fail closed. A fresh signed admission and a fresh pilot state are then required.

The report contains policy metadata, release identity, CI/Security workflow IDs and bounded job verdicts; it never contains the GitHub token, workflow logs or raw job payloads. Verification of an already signed report does not require the token.

## Evidence handling

The generated JSON and Markdown files are local/private and ignored by Git:

- `docs/pilot/repository_governance_report.json`
- `docs/pilot/repository_governance_report.md`

Do not place GitHub tokens, provider secrets, raw Telegram initData, cookies, authorization headers, workflow logs, or private customer identifiers in these files.
