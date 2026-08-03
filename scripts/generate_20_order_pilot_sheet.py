#!/usr/bin/env python3
import csv
from pathlib import Path

from pilot_control import SCENARIOS


out = Path("docs/20_order_pilot_sheet.csv")
out.parent.mkdir(parents=True, exist_ok=True)
with out.open("w", newline="", encoding="utf-8") as handle:
    writer = csv.writer(handle)
    writer.writerow(
        [
            "#",
            "wave",
            "scenario",
            "critical",
            "expected order status",
            "must verify",
            "result",
            "order id",
            "payment id",
            "refund id",
            "evidence",
            "comment",
        ]
    )
    for scenario in SCENARIOS:
        writer.writerow(
            [
                f"{scenario['number']:02d}",
                scenario["wave"],
                scenario["title"],
                "yes" if scenario["critical"] else "no",
                scenario.get("expected_order_status", "n/a"),
                scenario["must_verify"],
                "",
                "",
                "",
                "",
                "",
                "",
            ]
        )

Path("docs/20_order_pilot_sheet.md").write_text(
    "# FLASHIN — 20 order pilot sheet\n\n"
    + "\n".join(
        f"- [ ] {scenario['number']:02d}. Wave {scenario['wave']} — {scenario['title']}"
        f" {'(critical)' if scenario['critical'] else ''} — verify: {scenario['must_verify']}"
        for scenario in SCENARIOS
    )
    + "\n",
    encoding="utf-8",
)
print({"written": str(out), "scenarios": len(SCENARIOS)})
