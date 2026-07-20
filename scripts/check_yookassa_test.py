#!/usr/bin/env python3
import os
import sys
import uuid
import json
import urllib.request
import urllib.error
import base64

def main() -> None:
    shop_id = os.getenv("YOOKASSA_SHOP_ID")
    secret = os.getenv("YOOKASSA_SECRET_KEY")
    return_url = os.getenv("YOOKASSA_RETURN_URL", "https://mini.flashin.store/payment-result")

    if not shop_id or not secret:
        print("YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY are required")
        sys.exit(1)

    payload = {
        "amount": {"value": "1.00", "currency": "RUB"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": return_url},
        "description": "FLASHIN YooKassa test payment",
        "metadata": {"preflight": "true"},
    }
    data = json.dumps(payload).encode()
    auth = base64.b64encode(f"{shop_id}:{secret}".encode()).decode()
    req = urllib.request.Request(
        "https://api.yookassa.ru/v3/payments",
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Idempotence-Key": str(uuid.uuid4()),
            "Authorization": f"Basic {auth}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode())
            print("YooKassa test payment created")
            print("payment_id:", body.get("id"))
            print("status:", body.get("status"))
            print("confirmation_url:", body.get("confirmation", {}).get("confirmation_url"))
    except urllib.error.HTTPError as exc:
        print("YooKassa check failed", exc.code, exc.read().decode())
        sys.exit(1)
    except Exception as exc:
        print("YooKassa check failed", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
