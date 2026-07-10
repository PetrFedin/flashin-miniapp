from ..config import get_settings


def configure_products_index() -> dict:
    settings = get_settings()
    if not settings.meilisearch_enabled:
        return {"enabled": False}
    import meilisearch
    client = meilisearch.Client(settings.meilisearch_url, settings.meilisearch_master_key)
    index = client.index(settings.meilisearch_products_index)
    index.update_searchable_attributes(["title", "sku", "brand", "category", "description"])
    index.update_filterable_attributes(["brand", "category", "gender", "active", "price"])
    index.update_sortable_attributes(["price", "id"])
    index.update_ranking_rules(["words", "typo", "proximity", "attribute", "sort", "exactness"])
    return {"enabled": True, "index": settings.meilisearch_products_index}
