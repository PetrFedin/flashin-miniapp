import pytest
from fastapi import HTTPException

from backend.api.health import release


def test_release_identity_prefers_render_deploy_sha(monkeypatch):
    render_sha = "a" * 40
    fallback_sha = "b" * 40
    monkeypatch.setenv("RENDER_GIT_COMMIT", render_sha)
    monkeypatch.setenv("APP_GIT_SHA", fallback_sha)

    assert release() == {"git_sha": render_sha}


def test_release_identity_supports_portable_fallback(monkeypatch):
    fallback_sha = "c" * 40
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    monkeypatch.setenv("APP_GIT_SHA", fallback_sha)

    assert release() == {"git_sha": fallback_sha}


def test_release_identity_fails_closed_when_revision_is_missing(monkeypatch):
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    monkeypatch.delenv("APP_GIT_SHA", raising=False)

    with pytest.raises(HTTPException) as exc:
        release()

    assert exc.value.status_code == 503
    assert exc.value.detail == "Release identity unavailable"
