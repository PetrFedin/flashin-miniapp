# Incident: Fulfillment / SLA

## Symptoms

- Paid orders do not create fulfillment tasks.
- Pick-list missing.
- SLA events are overdue.
- Ready status not updated.

## Immediate actions

1. Check paid order status.
2. Check fulfillment task list.
3. Run SLA worker:
   ```bash
   docker compose --profile workers run --rm sla_jobs
   ```
4. Notify operations lead.

## Recovery

- Manually create task only after order payment is verified.
- Mark item-level issue if stock is missing or damaged.
