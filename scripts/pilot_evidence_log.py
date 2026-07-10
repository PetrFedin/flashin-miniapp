#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime
import csv

rows = [
    ["auth", "Telegram auth", "screenshot / log", ""],
    ["catalog", "Catalog opens", "screenshot", ""],
    ["product", "Product card opens", "screenshot", ""],
    ["cart", "Add to cart", "cart id / screenshot", ""],
    ["checkout", "Checkout created", "order id", ""],
    ["payment", "YooKassa payment created", "payment id", ""],
    ["webhook", "payment.succeeded received", "webhook log", ""],
    ["order_paid", "Order marked paid", "order id", ""],
    ["stock", "Stock decremented", "variant before/after", ""],
    ["fulfillment", "Fulfillment task created", "task id", ""],
    ["delivery", "Shipment/tracking tested", "shipment id", ""],
    ["refund", "Refund approved", "refund id", ""],
    ["loyalty", "Loyalty returned", "points before/after", ""],
    ["support", "Support ticket created", "ticket id", ""],
    ["privacy", "Privacy export works", "export json", ""],
]

out = Path("docs/pilot/pilot_evidence_log.csv")
with out.open("w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["area", "check", "evidence_required", "evidence_link_or_note"])
    writer.writerows(rows)

Path("docs/pilot/pilot_evidence_log.md").write_text(
    "# Pilot Evidence Log\n\n"
    + "\n".join([f"- [ ] {r[0]} — {r[1]} — evidence: {r[2]}" for r in rows])
    + "\n",
    encoding="utf-8",
)
print({"written": str(out)})
