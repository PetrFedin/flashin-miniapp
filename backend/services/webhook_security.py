import ipaddress
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException, Request

from ..config import get_settings


_INTERNAL_SCHEME = "internal"
_BLOCKED_HOSTS = {"localhost", "localhost.localdomain", "metadata.google.internal"}
_BLOCKED_SUFFIXES = (".local", ".internal", ".localhost")


def require_webhook_source(request: Request, allowed_ips: list[str] | None = None) -> None:
    """Reject a provider webhook when its source IP is outside an explicit allowlist."""
    if not allowed_ips:
        return
    client_ip = request.client.host if request.client else ""
    if client_ip not in allowed_ips:
        raise HTTPException(status_code=403, detail="Webhook source is not allowed")


def is_internal_destination(url: str) -> bool:
    try:
        return urlsplit((url or "").strip()).scheme.lower() == _INTERNAL_SCHEME
    except ValueError:
        return False


def _is_public_ip_literal(hostname: str) -> bool:
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def normalize_webhook_url(url: str, *, production: bool | None = None) -> str:
    raw = (url or "").strip()
    if not raw:
        raise ValueError("Webhook URL is required")
    if len(raw) > 2048:
        raise ValueError("Webhook URL is too long")

    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Webhook URL is invalid") from exc

    if parsed.scheme.lower() == _INTERNAL_SCHEME:
        raise ValueError("Internal destinations are not external webhooks")

    is_production = get_settings().app_env == "production" if production is None else production
    allowed_schemes = {"https"} if is_production else {"http", "https"}
    scheme = parsed.scheme.lower()
    if scheme not in allowed_schemes:
        expected = "https" if is_production else "http or https"
        raise ValueError(f"Webhook URL must use {expected}")
    if parsed.username or parsed.password:
        raise ValueError("Webhook URL must not contain credentials")
    if parsed.fragment:
        raise ValueError("Webhook URL must not contain a fragment")

    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        raise ValueError("Webhook URL hostname is required")
    if hostname in _BLOCKED_HOSTS or hostname.endswith(_BLOCKED_SUFFIXES):
        raise ValueError("Webhook URL hostname is not allowed")
    if not _is_public_ip_literal(hostname):
        raise ValueError("Webhook URL must not target a private or reserved IP address")

    default_port = 443 if scheme == "https" else 80
    netloc = hostname if port in (None, default_port) else f"{hostname}:{port}"
    path = parsed.path or "/"
    return urlunsplit((scheme, netloc, path, parsed.query, ""))
