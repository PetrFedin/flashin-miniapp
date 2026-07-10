#!/usr/bin/env python3
from pathlib import Path
import csv

rows = [
    ["01", "single item pickup no promo", "pickup", "-", "paid", "inventory decrement"],
    ["02", "single item courier no promo", "courier", "-", "paid", "delivery price"],
    ["03", "two items pickup", "pickup", "-", "paid", "multi item"],
    ["04", "two items courier", "courier", "-", "paid", "multi item delivery"],
    ["05", "promo percent pickup", "pickup", "FLASH10", "paid", "discount"],
    ["06", "promo percent courier", "courier", "FLASH10", "paid", "discount delivery"],
    ["07", "loyalty redeem", "pickup", "loyalty", "paid", "points deducted"],
    ["08", "loyalty refund", "courier", "loyalty", "refunded", "points returned"],
    ["09", "referral first paid order", "pickup", "referral", "paid", "reward"],
    ["10", "payment canceled", "courier", "-", "canceled", "reserve released"],
    ["11", "duplicate webhook", "pickup", "-", "paid", "idempotency"],
    ["12", "wishlist then cart", "pickup", "-", "paid", "wishlist"],
    ["13", "restock subscription", "pickup", "-", "pending", "notification"],
    ["14", "support ticket after order", "courier", "-", "paid", "support"],
    ["15", "privacy export", "pickup", "-", "paid", "json export"],
    ["16", "fulfillment picking", "pickup", "-", "paid", "task created"],
    ["17", "fulfillment issue", "courier", "-", "paid", "issue marked"],
    ["18", "SLA overdue test", "pickup", "-", "paid", "overdue"],
    ["19", "MoySklad conflict", "pickup", "-", "n/a", "conflict visible"],
    ["20", "slow network mobile", "courier", "FLASH10", "paid", "no broken state"],
]

out = Path("docs/20_order_pilot_sheet.csv")
with out.open("w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["#", "scenario", "delivery", "promo/loyalty/referral", "expected status", "must verify", "result", "comment"])
    for row in rows:
        writer.writerow(row + ["", ""])

Path("docs/20_order_pilot_sheet.md").write_text(
    "# 20 Order Pilot Sheet\n\n"
    + "\n".join([f"- [ ] {r[0]}. {r[1]} — verify: {r[5]}" for r in rows])
    + "\n",
    encoding="utf-8",
)
print({"written": str(out)})
