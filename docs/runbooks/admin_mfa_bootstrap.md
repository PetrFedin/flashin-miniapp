# Production administrator MFA bootstrap

FLASHIN requires enabled TOTP for every active production administrator. The first production database therefore uses a deliberately two-phase bootstrap: migrations may complete, but the release is not admitted and production services are not promoted until administrator MFA passes the deploy gate.

## First production database

If `scripts/check_admin_mfa.py` reports that no active administrator exists, keep the deployment stopped at that gate and run:

```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml run --rm backend python scripts/seed_admin.py
docker compose -f docker-compose.yml -f docker-compose.production.yml run --rm backend python scripts/provision_admin_totp.py --acknowledge-production-mfa-bootstrap
```

The bootstrap command targets `ADMIN_EMAIL` by default. `--email` may select another existing active administrator. The TOTP secret and the current six-digit verification code are read from hidden terminal prompts; neither is accepted on the command line or written to the audit payload.

After the command succeeds, wait for a new TOTP code because the enrollment counter is consumed immediately. Rerun the same retained production release deployment. The deploy gate must then verify MFA before release promotion.

## Security invariants

The offline bootstrap is intentionally narrower than the authenticated admin security API:

- `APP_ENV` must be `production` and explicit operator acknowledgement is mandatory.
- The target administrator must already exist and be active; this command never creates an administrator.
- The complete administrator set is locked in stable order so concurrent first-bootstrap attempts serialize.
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
