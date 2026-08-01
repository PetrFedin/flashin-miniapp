#!/usr/bin/env python3
import json
from pathlib import Path

from script_time import utc_timestamp

steps = [
    {"id": "P01", "title": "Open Mini App in Telegram", "critical": True},
    {"id": "P02", "title": "Open catalog", "critical": True},
    {"id": "P03", "title": "Open product card", "critical": True},
    {"id": "P04", "title": "Add product to cart", "critical": True},
    {"id": "P05", "title": "Apply promo code if available", "critical": False},
    {"id": "P06", "title": "Apply loyalty points if available", "critical": False},
    {"id": "P07", "title": "Apply referral code if available", "critical": False},
    {"id": "P08", "title": "Create checkout", "critical": True},
    {"id": "P09", "title": "Create YooKassa payment", "critical": True},
    {"id": "P10", "title": "Complete test payment", "critical": True},
    {"id": "P11", "title": "Verify payment webhook", "critical": True},
    {"id": "P12", "title": "Verify order paid", "critical": True},
    {"id": "P13", "title": "Verify stock writeoff/reservation", "critical": True},
    {"id": "P14", "title": "Verify fulfillment task", "critical": True},
    {"id": "P15", "title": "Create support ticket", "critical": False},
    {"id": "P16", "title": "Create refund request", "critical": True},
    {"id": "P17", "title": "Approve refund", "critical": True},
    {"id": "P18", "title": "Verify loyalty points returned", "critical": False},
    {"id": "P19", "title": "Verify admin audit trail", "critical": True},
    {"id": "P20", "title": "Verify customer notification", "critical": False},
]

report = {
    "created_at": utc_timestamp(),
    "steps": [{**step, "status": "todo", "comment": ""} for step in steps],
}

Path("docs/pilot/live_pilot_runner.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
Path("docs/pilot/live_pilot_runner.md").write_text(
    "# Live Pilot Runner\n\n"
    + "\n".join(
        [
            f"- [ ] {step['id']} — {step['title']} {'(critical)' if step['critical'] else ''}"
            for step in steps
        ]
    )
    + "\n",
    encoding="utf-8",
)
print({"written": "docs/pilot/live_pilot_runner.md", "steps": len(steps)})
