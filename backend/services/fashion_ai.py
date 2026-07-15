from __future__ import annotations

from typing import Any


"""
FLASHIN Fashion AI foundation.

Provider-agnostic layer for future integration with LLM/vision models.
Keeps business logic independent from a specific AI vendor.
"""


def build_product_style_context(product: dict[str, Any]) -> dict[str, Any]:
    """Prepare normalized context for AI stylist and recommendations."""
    return {
        "brand": product.get("brand", "FLASHIN"),
        "category": product.get("category", ""),
        "gender": product.get("gender", ""),
        "color": product.get("color", ""),
        "material": product.get("material", ""),
        "season": product.get("season", ""),
        "price": product.get("price", 0),
        "tags": product.get("tags", []),
    }


def generate_style_prompts(product: dict[str, Any]) -> list[str]:
    """Return prompt templates for AI outfit generation."""
    context = build_product_style_context(product)
    category = context["category"] or "item"

    return [
        f"Create a premium fashion look using this {category}.",
        "Suggest matching colors, footwear and accessories.",
        "Explain occasions where this item works best.",
        "Recommend alternative sizes or similar products if needed.",
    ]


def calculate_style_match_score(
    product_tags: list[str],
    customer_preferences: list[str],
) -> int:
    """Simple deterministic baseline before ML recommendations are connected."""
    if not product_tags or not customer_preferences:
        return 0

    matches = len(set(product_tags).intersection(set(customer_preferences)))
    return min(100, round(matches / max(len(customer_preferences), 1) * 100))
