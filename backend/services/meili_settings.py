from ..config import get_settings
from .meili import _client


def configure_products_index() -> dict:
    settings = get_settings()
    client = _client()
    if not client:
        return {"enabled": False}
    index = client.index(settings.meilisearch_products_index)
    index.update_searchable_attributes(["title", "sku", "brand", "category", "description"])
    index.update_filterable_attributes(["brand", "category", "gender", "active", "price"])
    index.update_sortable_attributes(["price", "id"])
    index.update_ranking_rules(["words", "typo", "proximity", "attribute", "sort", "exactness"])
    return {"enabled": True, "index": settings.meilisearch_products_index}
