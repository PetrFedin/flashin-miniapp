def test_meili_settings_imports():
    from backend.services.meili_settings import configure_products_index
    assert callable(configure_products_index)
