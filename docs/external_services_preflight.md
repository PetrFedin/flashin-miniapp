# External services preflight

## Domains

Run:

```bash
python scripts/check_domains.py
```

Expected:

```text
https://mini.flashin.store       200
https://api.flashin.store/health 200
https://admin.flashin.store      200
```

## YooKassa test mode

Set test credentials:

```env
YOOKASSA_SHOP_ID=
YOOKASSA_SECRET_KEY=
YOOKASSA_RETURN_URL=https://mini.flashin.store/payment-result
```

Run:

```bash
python scripts/check_yookassa_test.py
```

This creates a 1 RUB test payment and prints confirmation URL.

## R2/S3 media

Set:

```env
MEDIA_STORAGE=r2
S3_ENDPOINT_URL=
S3_BUCKET=
S3_ACCESS_KEY_ID=
S3_SECRET_ACCESS_KEY=
MEDIA_PUBLIC_BASE_URL=https://cdn.flashin.store
```

Run:

```bash
python scripts/check_r2_s3.py
```

The script uploads, downloads and deletes a small test object.

## BotFather

See:

```text
docs/botfather_domain_setup.md
```

## 20 order pilot

Generate scenario list:

```bash
python scripts/generate_test_orders_plan.py
```

Then process each order manually through Telegram + YooKassa test mode.
