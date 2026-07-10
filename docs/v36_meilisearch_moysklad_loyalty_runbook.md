# v36 runbook

## MoySklad mapping

Example: normalize size from MoySklad to storefront:

```json
{
  "source_field": "size",
  "source_value": "44-46",
  "target_field": "size",
  "target_value": "M",
  "active": true
}
```

Example: normalize category:

```json
{
  "source_field": "category",
  "source_value": "Одежда/Пиджаки",
  "target_field": "category",
  "target_value": "Jackets",
  "active": true
}
```

## Loyalty redemption

Apply points:

```http
POST /api/cart/loyalty
```

```json
{
  "points": 500
}
```

Rules:

- points must exist in CRM profile;
- discount cannot exceed `LOYALTY_MAX_REDEEM_PERCENT`;
- actual subtraction happens only after YooKassa `payment.succeeded`.

## Referral onboarding

Apply referral code:

```http
POST /api/cart/referral
```

```json
{
  "code": "FLABC12345"
}
```

Reward is issued after first paid order.

## Meilisearch

Start:

```bash
docker compose --profile search up -d meilisearch
```

Enable:

```env
MEILISEARCH_ENABLED=true
MEILISEARCH_MASTER_KEY=strong-secret
```

Rebuild:

```http
POST /api/search/admin/rebuild
```

## Metrics

Open:

```text
https://api.flashin.store/metrics
```

Prometheus already scrapes backend if monitoring profile is enabled.

## Tests

Run:

```bash
pytest backend/tests
```
