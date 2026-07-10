from fastapi import HTTPException, Request


def require_webhook_source(request: Request, allowed_ips: list[str] | None = None) -> None:
    """Basic hook for webhook source validation.

    For production, configure reverse proxy to pass the real client IP and add
    provider-specific verification if available.
    """
    if not allowed_ips:
        return
    client_ip = request.client.host if request.client else ""
    if client_ip not in allowed_ips:
        raise HTTPException(status_code=403, detail="Webhook source is not allowed")
