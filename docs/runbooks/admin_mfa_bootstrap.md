# Production administrator MFA bootstrap

FLASHIN requires enabled TOTP for every active production administrator. The first production database therefore uses a deliberately two-phase bootstrap: migrations may complete, but the release is not admitted and production services are not promoted until administrator MFA passes the deploy gate.

## First production database

`ADMIN_PASSWORD` must not be stored in the production environment. The production configuration and environment validator reject it. The first owner password exists only at the interactive bootstrap boundary.

If `scripts/check_admin_mfa.py` reports that no active administrator exists, keep the deployment stopped at that gate and run:

```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml run --rm backend python scripts/seed_admin.py --acknowledge-production-admin-bootstrap
docker compose -f docker-compose.yml -f docker-compose.production.yml run --rm backend python scripts/provision_admin_totp.py --acknowledge-production-mfa-bootstrap
```

The administrator seed targets `ADMIN_EMAIL` by default. It prompts twice for the first owner password with terminal echo disabled. The TOTP bootstrap then targets the same existing administrator by default and prompts for the TOTP secret and current six-digit verification code without echo. None of these authentication factors is accepted as a command-line argument or written to the audit payload.

After the commands succeed, wait for a new TOTP code because the enrollment counter is consumed immediately. Rerun the same retained production release deployment. The deploy gate must then verify MFA before release promotion.

## Administrator seed invariants

The offline administrator seed is a first-database bootstrap, not a general account-management command:

- production requires `--acknowledge-production-admin-bootstrap` and an interactive terminal;
- `ADMIN_PASSWORD` is forbidden in production and the password is prompted twice without echo;
- the same administrator password policy used by password reset is enforced;
- a PostgreSQL transaction advisory lock serializes the first-admin decision even while `admin_users` is empty;
- existing administrator rows are then locked before their state is evaluated;
- when the table is empty, the command creates exactly one active `owner`;
- once any administrator exists, the command never creates another account or changes a role/password/active flag;
- rerunning for the same existing active owner is an idempotent no-op;
- a different email or a non-active/non-owner bootstrap record fails closed;
- only non-secret bootstrap metadata is written to the audit log.

Additional administrators must be created or managed through the authenticated administrative security workflow, never by rerunning the bootstrap seed.

## MFA bootstrap invariants

The offline MFA bootstrap is intentionally narrower than the authenticated admin security API:

- `APP_ENV` must be `production` and explicit operator acknowledgement is mandatory.
- The target administrator must already exist and be active; this command never creates an administrator.
- The complete administrator set is locked in stable order so concurrent first-factor bootstrap attempts serialize.
- The command refuses to run once any active administrator already has enabled MFA.
- The submitted TOTP code must match an exact counter and that counter is consumed in the same transaction as enrollment.
- The TOTP secret is encrypted through the existing `ADMIN_TOTP_ENCRYPTION_KEY` path.
- Existing administrator sessions are revoked when MFA is enabled.
- The transaction is rolled back on any failure.
- Only non-secret bootstrap metadata is written to the audit log.

Once one active administrator has MFA, use the authenticated `/api/admin-security/totp/{admin_id}` security workflow for enrollment or rotation. Do not use the offline bootstrap as a general MFA reset mechanism.

## Multiple active administrators

The production deploy gate requires MFA for every active administrator. If an existing database has several active administrators and at least one already has MFA, sign in as an MFA-enabled security administrator and configure the remaining accounts through the authenticated admin security API before retrying deployment.

If no active administrator has MFA, bootstrap exactly one existing active administrator offline, then use that authenticated administrator for the remaining accounts. Do not weaken or bypass the deploy gate.
