"""Product content quality helpers for FLASHIN Telegram Mini App.

Used by PIM and admin workflows to determine whether a fashion item is ready
for publication.
"""

from __future__ import annotations

from typing import Any


QUALITY_WEIGHTS = {
    "title": 15,
    "description": 15,
    "images": 20,
    "variants": 20,
    "size_chart": 10,
    "materials": 10,
    "seo": 10,
}


def calculate_product_quality(product: Any) -> dict[str, Any]:
    checks = {
        "title": bool(getattr(product, "title", "")),
        "description": len(getattr(product, "description", "") or "") >= 80,
        "images": len(getattr(product, "images", []) or []) >= 3,
        "variants": len(getattr(product, "variants", []) or []) > 0,
        "size_chart": bool(getattr(product, "size_chart", None)),
        "materials": bool(getattr(product, "materials", None)),
        "seo": bool(getattr(product, "seo_title", None) or getattr(product, "seo_description", None)),
    }
    score = sum(QUALITY_WEIGHTS[key] for key, value in checks.items() if value)
    return {
        "score": score,
        "ready_for_publication": score >= 85,
        "checks": checks,
        "missing": [key for key, value in checks.items() if not value],
    }
