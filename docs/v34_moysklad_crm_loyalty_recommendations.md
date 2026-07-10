# FLASHIN v34 — MoySklad, CRM, Loyalty, Recommendations, BI

## Added

### MoySklad connector

Files:

```text
backend/services/moysklad.py
backend/api/moysklad.py
backend/jobs/moysklad_jobs.py
scripts/run_moysklad_sync.py
```

Config:

```env
MOYSKLAD_BASE_URL=https://api.moysklad.ru/api/remap/1.2
MOYSKLAD_TOKEN=
MOYSKLAD_LOGIN=
MOYSKLAD_PASSWORD=
```

Priority:

1. `MOYSKLAD_TOKEN`
2. Basic Auth from `MOYSKLAD_LOGIN` + `MOYSKLAD_PASSWORD`

Sync endpoint:

```text
POST /api/moysklad/sync
```

Logs:

```text
GET /api/moysklad/sync-logs
```

### CRM

Files:

```text
backend/services/crm.py
backend/api/crm.py
```

CRM profile includes:

- segment;
- orders count;
- total spent;
- average order value;
- loyalty points;
- VIP flag.

Endpoints:

```text
POST /api/crm/recompute
GET  /api/crm/profiles
```

### Loyalty

Loyalty points are calculated by:

```env
LOYALTY_POINTS_PER_RUBLE=0.01
```

Example:

```text
100000 RUB spent -> 1000 points
```

### Recommendations

Files:

```text
backend/services/recommendations.py
backend/api/recommendations.py
```

Endpoints:

```text
GET  /api/recommendations/{product_id}
POST /api/recommendations/admin/rebuild
```

Current algorithm:

```text
same category first, then fallback to active products
```

### Size helper

Endpoint:

```text
POST /api/recommendations/size-helper
```

Inputs:

- height;
- weight;
- usual size;
- fit preference.

### Business analytics

Endpoint:

```text
GET /api/business-analytics/summary
```

Returns:

- GMV;
- orders count;
- AOV;
- customers;
- active carts.

### Monitoring

Added Prometheus/Grafana profile:

```bash
docker compose --profile monitoring up -d
```

Prometheus:

```text
http://localhost:9090
```

Grafana:

```text
http://localhost:3000
```

## Important limitation

MoySklad assortment API structure can differ depending on account setup, variants, custom attributes and stock reports. The connector is production-ready as a starting layer, but first sync must be tested on your real account and mapped against your actual SKU/size/color conventions.
