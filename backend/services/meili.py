from ..config import get_settings
from ..models import Product


def _client():
    settings = get_settings()
    if not settings.meilisearch_enabled:
        return None
    import meilisearch
    return meilisearch.Client(settings.meilisearch_url, settings.meilisearch_master_key)


def product_document(product: Product) -> dict:
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


def index_products(products: list[Product]) -> int:
    client = _client()
    if not client:
        return 0
    settings = get_settings()
    index = client.index(settings.meilisearch_products_index)
    docs = [product_document(p) for p in products]
    if docs:
        index.add_documents(docs, primary_key="id")
    return len(docs)


def search_products_meili(query: str, limit: int = 20) -> list[int]:
    client = _client()
    if not client:
        return []
    settings = get_settings()
    index = client.index(settings.meilisearch_products_index)
    result = index.search(query, {"limit": limit, "filter": ["active = true"]})
    return [hit["id"] for hit in result.get("hits", [])]
