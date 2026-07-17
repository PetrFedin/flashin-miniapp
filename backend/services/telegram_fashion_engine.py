from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FashionRecommendation:
    title: str
    reason: str
    score: int
    action: str


class TelegramFashionEngine:
    """Business logic foundation for FLASHIN Telegram Mini App recommendations.

    This layer is intentionally framework independent. It can later be connected
    to embeddings, LLMs, customer history and catalog analytics.
    """
