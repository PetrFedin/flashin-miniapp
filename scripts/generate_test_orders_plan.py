#!/usr/bin/env python3
"""Generate manual QA plan for 20 pilot orders.

This does not fake orders in production. It prints a scenario matrix for real Telegram/YooKassa test mode.
"""
SCENARIOS = [
    ("single item pickup no promo", "pickup", "", 1),
    ("single item courier no promo", "courier", "", 1),
    ("two items pickup", "pickup", "", 2),
    ("two items courier", "courier", "", 2),
    ("promo percent pickup", "pickup", "FLASH10", 1),
    ("promo percent courier", "courier", "FLASH10", 1),
    ("out of stock attempt", "pickup", "", 1),
    ("cancel before payment", "pickup", "", 1),
    ("payment canceled", "courier", "", 1),
    ("payment succeeded", "courier", "", 1),
    ("duplicate webhook", "courier", "", 1),
    ("wishlist then cart", "pickup", "", 1),
    ("restock subscribe", "pickup", "", 1),
    ("return request", "courier", "", 1),
    ("refund approve", "courier", "", 1),
    ("admin status ready", "pickup", "", 1),
    ("admin status shipped", "courier", "", 1),
    ("abandoned cart", "pickup", "", 1),
    ("low stock", "pickup", "", 1),
    ("mobile slow network", "courier", "FLASH10", 1),
]

print("# FLASHIN 20 test orders plan")
for idx, (name, delivery, promo, items) in enumerate(SCENARIOS, start=1):
    print(f"{idx:02d}. {name}")
    print(f"    delivery={delivery}; promo={promo or '-'}; items={items}")
    print("    Verify: order status, payment status, inventory, notification, audit/analytics where applicable")
