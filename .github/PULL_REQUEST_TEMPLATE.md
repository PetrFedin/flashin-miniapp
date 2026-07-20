# FLASHIN Pull Request

## Summary

Describe what changed and why.

## Scope

- [ ] Backend
- [ ] Storefront
- [ ] Admin
- [ ] Bot / Telegram
- [ ] Database / migration
- [ ] Infrastructure / CI
- [ ] Documentation only

## Risk assessment

- [ ] No breaking API changes
- [ ] No schema change
- [ ] No secrets or credentials added
- [ ] No public endpoint unintentionally introduced
- [ ] Rollback path is documented

## Security checklist

- [ ] Authentication and authorization were reviewed
- [ ] Input validation was reviewed
- [ ] Sensitive data is not logged
- [ ] Webhook signatures and idempotency were considered where relevant
- [ ] Money calculations do not use binary floating-point for persisted amounts

## Database

Describe migrations, backfill, compatibility, and rollback. Write `N/A` when not applicable.

## Testing

List commands and scenarios executed.

- [ ] Backend tests
- [ ] Frontend build
- [ ] Admin build
- [ ] Docker build
- [ ] Manual Telegram Mini App smoke test

## Rollback

Describe how to revert this change safely.

## Deployment notes

List required environment variables, secrets, DNS, BotFather, YooKassa, MoySklad, R2/S3, or other external actions. Write `N/A` when not applicable.
