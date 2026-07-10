def test_fulfillment_service_imports():
    from backend.services.fulfillment import update_fulfillment_status
    assert callable(update_fulfillment_status)
