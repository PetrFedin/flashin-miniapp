def test_app_imports():
    from backend.main import app
    assert "FLASHIN Mini App Backend" in app.title


def test_enterprise_api_uses_canonical_feature_flag_model():
    from backend.api.enterprise import FeatureFlag as enterprise_feature_flag
    from backend.models import FeatureFlag

    assert enterprise_feature_flag is FeatureFlag
