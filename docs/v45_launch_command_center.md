# FLASHIN v45 — Launch Command Center

## Purpose

This document is the single launch control room for the FLASHIN Telegram Mini App.

## Launch roles

| Role | Responsibility |
|---|---|
| Launch owner | Go / no-go decision |
| Backend | API, DB, migrations, workers |
| Frontend | Mini App and Admin |
| Operations | orders, fulfillment, support |
| Finance | YooKassa, refunds |
| Inventory | MoySklad, stock reconciliation |
| Support | customer issues |

## Launch timeline

### T-7 days

- Fill production `.env`.
- Replace legal texts.
- Connect BotFather domain.
- Connect YooKassa test mode.
- Connect MoySklad token.
- Upload real products.
- Run local pilot.

### T-3 days

- Run 20-order pilot.
- Verify backup restore.
- Verify rollback.
- Verify refund.
- Verify loyalty return.
- Verify fulfillment.
- Verify SLA worker.

### T-1 day

- Freeze code.
- Run readiness gate.
- Run integration checks.
- Export release manifest.
- Confirm support coverage.

### Launch day

```bash
python3 scripts/readiness_gate.py
make deploy-prod
make health
python tests/e2e_smoke.py
```

### First 2 hours

Monitor:

- payment webhooks;
- failed orders;
- MoySklad sync;
- fulfillment queue;
- support tickets;
- Sentry;
- logs.

## Go / no-go

GO only if:

- readiness gate is green;
- 20-order pilot passed;
- legal pages are final;
- backup restore tested;
- rollback tested;
- YooKassa webhook verified;
- MoySklad stock verified.
