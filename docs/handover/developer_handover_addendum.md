# Developer Handover Addendum

## Do first

```bash
python3 scripts/launch.py --mode local --with-search --with-workers
python3 scripts/connected_system_audit.py
python3 scripts/simplicity_score.py
```

## Main code areas

| Area | Files |
|---|---|
| Auth | `backend/api/auth.py`, `backend/security.py` |
| Catalog | `backend/api/products.py` |
| Cart | `backend/api/cart.py` |
| Orders | `backend/api/orders.py` |
| Payments | `backend/api/payments.py`, `backend/api/payment_reconciliation.py` |
| Delivery | `backend/api/delivery_providers.py`, `backend/api/delivery_quotes.py` |
| Fulfillment | `backend/api/fulfillment.py` |
| MoySklad | `backend/services/moysklad.py`, `backend/api/moysklad_deep_mapping.py` |
| Media | `backend/api/media.py`, `backend/jobs/media_jobs.py` |
| Admin | `admin/src/main.jsx` |
| Mini App | `frontend/src/App.js` |

## Rule

Do not add new modules before running the real 20-order pilot. Fix only real breakages after this point.
