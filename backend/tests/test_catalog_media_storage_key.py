from backend.catalog_models import managed_storage_key_from_url


def test_managed_media_url_recovers_object_key_without_query_string():
    assert managed_storage_key_from_url(
        "https://cdn.flashin.store/products/20260814/item.webp?version=2",
        "https://cdn.flashin.store",
    ) == "products/20260814/item.webp"


def test_external_media_url_never_receives_managed_storage_key():
    assert managed_storage_key_from_url(
        "https://partner.example/item.webp",
        "https://cdn.flashin.store",
    ) == ""


def test_encoded_path_traversal_is_rejected():
    assert managed_storage_key_from_url(
        "https://cdn.flashin.store/products/%2e%2e/private.webp",
        "https://cdn.flashin.store",
    ) == ""
