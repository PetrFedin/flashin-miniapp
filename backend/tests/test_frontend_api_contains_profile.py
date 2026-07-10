from pathlib import Path

def test_frontend_api_contains_profile():
    text = Path("frontend/src/api.js").read_text()
    assert "getProfile" in text
    assert "applyLoyalty" in text
    assert "createSupportTicket" in text
