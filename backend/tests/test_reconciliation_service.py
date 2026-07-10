def test_reconciliation_service_imports():
    from backend.services.stock_reconciliation import reconcile_stock_rows
    assert callable(reconcile_stock_rows)
