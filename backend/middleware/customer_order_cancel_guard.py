import re


class CustomerOrderCancelGuardMiddleware:
    """Keep the public cancellation URL while routing it to the safe workflow."""

    _LEGACY_PATH = re.compile(r"^/api/orders/(\d+)/cancel/?$")

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and str(scope.get("method") or "").upper() == "POST":
            path = str(scope.get("path") or "")
            match = self._LEGACY_PATH.fullmatch(path)
            if match:
                rewritten = f"/api/orders/{match.group(1)}/cancel-safe"
                scope = dict(scope)
                scope["path"] = rewritten
                scope["raw_path"] = rewritten.encode("ascii")
        await self.app(scope, receive, send)
