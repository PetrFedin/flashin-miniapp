# FLASHIN v48 — deep hardening layer

## What was deepened after v47

### Real E2E

Added guarded runner:

```text
backend/tests/e2e/test_real_order_flow_runner.py
```

It can hit a running stack with real `CUSTOMER_TOKEN` and `ADMIN_TOKEN`.

### Admin security

Added API:

```text
/api/admin-security/login-events
/api/admin-security/sessions
/api/admin-security/sessions/revoke/{admin_id}
/api/admin-security/password-reset/{admin_id}
/api/admin-security/totp/{admin_id}
/api/admin-security/ip-allowlist
```

Added CSP/security headers middleware.

### Payment reconciliation

v47 already added reconciliation entities. v48 keeps it wired and documented for admin UX.

### Delivery

Added public quote endpoint:

```text
/api/delivery-quotes?provider_code=cdek&zone=moscow
```

### Admin UX

Added frontend endpoint map:

```text
admin/src/adminEndpoints.js
```

This gives developers a clear map for order/customer/product cards, filters and operation screens.

### Observability

Added separate Grafana dashboard scaffolds:

```text
flashin_payments.json
flashin_fulfillment.json
flashin_operations.json
```

### MoySklad deep mapping

Added v2 mapping helper:

```text
backend/services/moysklad_deep_mapping_v2.py
```

Supports centralized hooks for:

- category;
- brand;
- gender;
- size;
- prices;
- safety stock.

### Media pipeline

Added:

```text
backend/services/cdn.py
frontend/public/fallback-product.svg
```

CDN purge hook is configurable; fallback image is now local.

### Security audit

v47 added `security_audit.sh`; v48 adds security headers.

### Load/stress

Added richer k6 scripts:

```text
k6_catalog_search_checkout.js
k6_webhook_burst.js
```

## Remaining before live launch

These are not code gaps; they need real credentials and real services:

- BotFather domain;
- YooKassa webhook;
- MoySklad production token;
- R2/S3 bucket;
- final legal text;
- live 20-order pilot.
