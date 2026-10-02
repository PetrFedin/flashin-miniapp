from __future__ import annotations

import os


def runtime_git_sha() -> str:
    """Return the immutable deployed source revision when the platform provides it."""

    return (
        os.getenv("RENDER_GIT_COMMIT", "").strip()
        or os.getenv("APP_GIT_SHA", "").strip()
    )


__all__ = ["runtime_git_sha"]
