def test_metrics_imports():
    from backend.middleware.metrics import metrics_response
    assert callable(metrics_response)
