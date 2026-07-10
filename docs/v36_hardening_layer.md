# FLASHIN v36 — hardening layer

## Added

### 1. MoySklad mapping

New tables:

```text
moysklad_mapping_rules
moysklad_conflicts
```

Admin endpoints:

```text
POST /api/admin/moysklad/mapping-rules
GET  /api/admin/moysklad/mapping-rules
GET  /api/admin/moysklad/conflicts
```

Mapping now supports:

- size normalization;
- category normalization;
- conflict logging for missing SKU/article/code.

### 2. Loyalty redemption

Cart now stores:

```text
loyalty_points_to_redeem
```

Order now stores:

```text
loyalty_points_redeemed
loyalty_discount_amount
```

Endpoints:

```text
POST /api/cart/loyalty
GET  /api/loyalty/transactions
```

Rules:

- points are reserved in cart;
- points are subtracted only after payment success;
- max redemption percentage is controlled by env.

### 3. Referral onboarding

New table:

```text
referral_attributions
```

Endpoint:

```text
POST /api/cart/referral
```

Referral reward is paid only after invited customer's first paid order.

### 4. Meilisearch

Added optional Meilisearch support.

Config:

```env
MEILISEARCH_ENABLED=true
MEILISEARCH_URL=http://meilisearch:7700
MEILISEARCH_MASTER_KEY=...
```

Docker:

```bash
docker compose --profile search up -d meilisearch
```

Search endpoint first tries Meilisearch, then falls back to internal DB index.

### 5. Admin hardening

Added admin endpoints:

- product detail;
- product update;
- customer list;
- customer detail;
- MoySklad mapping rules;
- MoySklad conflicts.

Admin UI shows:

- mapping rules;
- MoySklad conflicts;
- customers.

### 6. Prometheus metrics

Added:

```text
/metrics
```

Metrics:

```text
flashin_http_requests_total
flashin_http_request_latency_seconds
```

### 7. Critical tests

Added tests for:

- size helper;
- Meilisearch document shape;
- MoySklad stock parser.

## Important

This version removes the weakest architectural gaps from v35:

```text
MoySklad is now mappable
Loyalty can be spent
Referral has attribution
Search can move to Meilisearch
Metrics endpoint exists
Admin can inspect product/customer details
```
