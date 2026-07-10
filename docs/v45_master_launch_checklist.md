# FLASHIN v45 — Master Launch Checklist

## Code and package

- [ ] Latest package is unpacked.
- [ ] `.env` exists.
- [ ] `python3 scripts/preflight.py` passes.
- [ ] `python3 scripts/validate_env.py` passes.
- [ ] `python3 scripts/readiness_gate.py` passes.
- [ ] `make test` passes.
- [ ] `python tests/e2e_smoke.py` passes.

## Infrastructure

- [ ] PostgreSQL running.
- [ ] Backend healthy.
- [ ] Frontend healthy.
- [ ] Admin healthy.
- [ ] Bot running.
- [ ] Caddy/HTTPS configured.
- [ ] Monitoring running.
- [ ] Backups configured.
- [ ] Backup restore tested.
- [ ] Rollback tested.

## Integrations

- [ ] BotFather domain set.
- [ ] YooKassa test payment works.
- [ ] YooKassa webhook works.
- [ ] MoySklad sync works.
- [ ] MoySklad mapping verified.
- [ ] R2/S3 upload works.
- [ ] Meilisearch configured.

## Product content

- [ ] 5–10 real products uploaded.
- [ ] Images checked.
- [ ] Prices checked.
- [ ] Sizes checked.
- [ ] Stock checked.
- [ ] Categories checked.
- [ ] Looks checked.

## Legal

- [ ] Offer finalized.
- [ ] Privacy policy finalized.
- [ ] Return rules finalized.
- [ ] Contacts and legal entity filled.
- [ ] Marketing consent wording approved.

## Pilot

- [ ] 20-order pilot completed.
- [ ] Refund tested.
- [ ] Loyalty redemption tested.
- [ ] Referral reward tested.
- [ ] Fulfillment tested.
- [ ] SLA overdue tested.
- [ ] Support ticket tested.
- [ ] Privacy export tested.

## Go

- [ ] Launch owner approved.
- [ ] Support operator ready.
- [ ] Operations operator ready.
- [ ] Finance/payment owner ready.
- [ ] Rollback owner ready.
