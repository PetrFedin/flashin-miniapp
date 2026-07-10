# KPI Dashboard Specification

## Executive dashboard

| Metric | Definition | Source |
|---|---|---|
| GMV | Sum of paid orders | orders |
| Orders | Count paid orders | orders |
| AOV | GMV / orders | orders |
| Conversion | paid orders / sessions | analytics_events |
| Refund rate | refunded orders / paid orders | orders |
| Loyalty redemption rate | orders with redeemed points / paid orders | orders |
| Referral activation | rewarded referrals / referral codes | referral_attributions |

## Operations dashboard

| Metric | Definition | Source |
|---|---|---|
| Fulfillment backlog | open fulfillment tasks | fulfillment_tasks |
| SLA overdue | overdue SLA events | sla_events |
| MoySklad conflicts | open conflicts | moysklad_conflicts |
| Payment failures | canceled/failed payments | payments |
| Webhook failures | failed outbox rows | webhook_outbox |
| Support backlog | open tickets | support_tickets |

## Product dashboard

| Metric | Definition | Source |
|---|---|---|
| Product views | product_view events | analytics_events |
| Add to cart | cart events | analytics_events |
| Stockouts | variants with zero available | product_variants |
| Low stock | available below threshold | product_variants |
| Search terms | search events | analytics_events |
