def test_app_imports():
    from backend.main import app
    assert "FLASHIN Mini App Backend" in app.title
