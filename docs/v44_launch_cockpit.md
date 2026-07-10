# FLASHIN v44 — Launch Cockpit

## One-time setup

```bash
python3 scripts/setup_wizard.py
python3 scripts/validate_env.py
make init
```

## Integration check

```bash
python3 scripts/check_integrations.py
```

Output:

```text
docs/integration_check_report.json
```

## Production readiness report

```bash
python3 scripts/production_readiness_report.py
```

Output:

```text
docs/production_readiness_report.md
docs/production_readiness_report.json
```

## 20-order pilot

```bash
python3 scripts/generate_20_order_pilot_sheet.py
```

Output:

```text
docs/20_order_pilot_sheet.csv
docs/20_order_pilot_sheet.md
```

## Launch sequence

1. Fill `.env`.
2. Validate environment.
3. Run preflight.
4. Run migrations.
5. Start app.
6. Configure BotFather domain.
7. Configure YooKassa webhook.
8. Configure MoySklad token.
9. Configure Meilisearch.
10. Upload 5 real products.
11. Run 20-order pilot.
12. Replace legal templates.
13. Deploy production.
14. Monitor first real orders.

## No-go

Do not launch publicly if any of these fail:

- Telegram auth;
- product sync;
- cart;
- checkout;
- YooKassa webhook;
- inventory writeoff;
- refund;
- loyalty return;
- fulfillment task;
- backup restore test.
