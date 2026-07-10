# FLASHIN v47 — security, e2e, delivery, observability hardening

## Added

### 1. Real E2E test scaffold

```text
backend/tests/e2e/
```

Covers scenario structure:

```text
cart -> checkout -> payment webhook -> fulfillment -> refund
```

### 2. Admin security

New tables:

```text
admin_login_events
admin_sessions
admin_password_resets
admin_totp_secrets
admin_ip_allowlist
```

Services:

```text
backend/services/admin_security.py
```

Covers:

- login journal;
- session revoke foundation;
- password reset tokens;
- TOTP secret storage foundation;
- IP allowlist.

### 3. Payment reconciliation

New table:

```text
payment_reconciliations
```

Endpoints:

```text
GET  /api/payment-reconciliation
POST /api/payment-reconciliation/payments/{payment_id}/check
POST /api/payment-reconciliation/{row_id}/resolve
```

### 4. Delivery providers

New tables:

```text
delivery_providers
delivery_shipments
```

Provider codes:

```text
courier
cdek
boxberry
pickup
```

Endpoints:

```text
GET  /api/delivery-providers
POST /api/delivery-providers
POST /api/delivery-providers/orders/{order_id}/shipment
PATCH /api/delivery-providers/shipments/{shipment_id}
GET  /api/delivery-providers/shipments
```

### 5. Admin UX hardening foundation

Backend endpoints now support:

- payment reconciliation;
- delivery shipments;
- delivery providers;
- MoySklad SKU matches;
- media processing jobs.

### 6. Production observability

Grafana dashboard scaffold:

```text
deploy/grafana/dashboards/flashin_operations.json
```

Panels:

- HTTP requests;
- p95 latency;
- webhook failures;
- payment failures;
- SLA overdue;
- MoySklad conflicts.

### 7. MoySklad deep mapping

New table:

```text
moysklad_sku_matches
```

Endpoints:

```text
GET  /api/moysklad-deep-mapping/sku-matches
POST /api/moysklad-deep-mapping/sku-matches/{match_id}/confirm
```

### 8. Media pipeline background jobs

New table:

```text
media_processing_jobs
```

Job:

```bash
python scripts/run_media_jobs.py
```

### 9. Security audit

Script:

```bash
scripts/security_audit.sh
```

Supports:

- pip-audit;
- gitleaks;
- trivy.

### 10. Load/stress testing

Added:

```text
deploy/loadtest/k6_catalog_search_checkout.js
deploy/loadtest/k6_webhook_burst.js
```

## Migration

```text
0009_security_payment_delivery_media_hardening.py
```
