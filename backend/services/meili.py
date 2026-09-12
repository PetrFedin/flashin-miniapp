from collections.abc import Mapping, Sequence
from typing import Any

from ..config import get_settings
from ..models import Product


def _client():
    settings = get_settings()
    if not settings.meilisearch_enabled:
        return None
    import meilisearch

    return meilisearch.Client(
        settings.meilisearch_url,
        settings.meilisearch_master_key,
        timeout=settings.meilisearch_timeout_seconds,
    )


def product_document(product: Product) -> dict[str, Any]:
    """Materialize the complete primitive search document while ORM state is available."""

    return {
        "id": product.id,
        "sku": product.sku,
        "title": product.title,
        "brand": product.brand,
        "description": product.description,
        "price": product.price,
        "currency": product.currency,
        "category": product.category,
        "gender": product.gender,
        "active": product.active,
        "image_url": product.images[0].url if product.images else "",
    }


def index_product_documents(documents: Sequence[Mapping[str, Any]]) -> int:
    """Send detached primitive documents to Meilisearch.

    Callers that originate from a database request must end their SQLAlchemy
    transaction before entering this provider boundary. The copies below make
    it impossible for this transport function to trigger lazy ORM access.
    """

    client = _client()
    if not client:
        return 0
    settings = get_settings()
    index = client.index(settings.meilisearch_products_index)
    docs = [dict(document) for document in documents]
    if docs:
        index.add_documents(docs, primary_key="id")
    return len(docs)


def index_products(products: list[Product]) -> int:
    """Compatibility helper for non-request callers.

    Request paths must snapshot ORM rows first and call
    ``index_product_documents`` only after their database transaction ends.
    """

    return index_product_documents([product_document(product) for product in products])


def search_products_meili(query: str, limit: int = 20) -> list[int]:
    client = _client()
    if not client:
        return []
    settings = get_settings()
    index = client.index(settings.meilisearch_products_index)
    result = index.search(query, {"limit": limit, "filter": ["active = true"]})
    return [hit["id"] for hit in result.get("hits", [])]
