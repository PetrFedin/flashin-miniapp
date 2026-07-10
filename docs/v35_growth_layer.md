# FLASHIN v35 — growth layer

## Added

### Loyalty ledger
- `loyalty_transactions`
- customer referral code
- points awarded after paid order

Endpoints:
- `GET /api/loyalty/transactions`
- `GET /api/loyalty/referral-code`

### Referral
- `referral_codes`
- referral reward points
- service-level apply function

### Marketing campaigns
- `marketing_campaigns`
- segment-based campaign queue
- messages are queued into Telegram notifications

Endpoints:
- `POST /api/campaigns`
- `POST /api/campaigns/{id}/queue`
- `GET /api/campaigns`

### Search
- internal `product_search_index`
- rebuild endpoint
- public product search endpoint

Endpoints:
- `GET /api/search/products?q=...`
- `POST /api/search/admin/rebuild`

### Look Builder
- `looks`
- `look_items`
- complete-the-look foundation

Endpoints:
- `GET /api/looks`
- `POST /api/looks`

### Customer timeline
- `customer_timeline_events`
- timeline event created after paid order

## Why this matters

v34 connected stock and CRM. v35 adds growth mechanics:

```text
customer buys -> loyalty points
customer profile -> segment
segment -> campaign
campaign -> notification
product search -> faster catalog discovery
look builder -> higher AOV
```

## Still possible next
- external search engine: Meilisearch/Elasticsearch;
- personal recommendations based on events;
- loyalty redemption in checkout;
- referral input in onboarding;
- campaign scheduler.
