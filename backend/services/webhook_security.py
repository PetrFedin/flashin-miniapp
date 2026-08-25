import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException, Request

from ..config import get_settings

_INTERNAL_SCHEME = "internal"
_MAX_WEBHOOK_URL_LENGTH = 255
_MAX_RESOLVED_ADDRESSES = 16
_BLOCKED_HOSTS = {"localhost", "localhost.localdomain", "metadata.google.internal"}
_BLOCKED_SUFFIXES = (".local", ".internal", ".localhost")
_REDACTED_WEBHOOK_URL = "<redacted>"


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


def redact_webhook_url(url: str) -> str:
    """Return only the destination origin; path/query values are treated as secrets.

    Webhook endpoints frequently embed opaque access tokens in their path or query.
    The stored URL is still used for delivery, but control-plane reads and audit
    records must not expose those credentials.
    """

    try:
        parsed = urlsplit((url or "").strip())
        scheme = parsed.scheme.lower()
        hostname = (parsed.hostname or "").strip().lower().rstrip(".")
        port = parsed.port
    except (TypeError, ValueError):
        return _REDACTED_WEBHOOK_URL

    if not scheme or not hostname:
        return _REDACTED_WEBHOOK_URL
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return _REDACTED_WEBHOOK_URL

    netloc_host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = 443 if scheme == "https" else 80 if scheme == "http" else None
    netloc = netloc_host if port in (None, default_port) else f"{netloc_host}:{port}"
    return urlunsplit((scheme, netloc, "/<redacted>", "", ""))


def _is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _is_public_ip_literal(hostname: str) -> bool:
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return _is_public_address(address)


def normalize_webhook_url(url: str, *, production: bool | None = None) -> str:
    raw = (url or "").strip()
    if not raw:
        raise ValueError("Webhook URL is required")
    if len(raw) > _MAX_WEBHOOK_URL_LENGTH:
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

    raw_hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not raw_hostname:
        raise ValueError("Webhook URL hostname is required")
    try:
        hostname = raw_hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("Webhook URL hostname is invalid") from exc
    if hostname in _BLOCKED_HOSTS or hostname.endswith(_BLOCKED_SUFFIXES):
        raise ValueError("Webhook URL hostname is not allowed")
    if not _is_public_ip_literal(hostname):
        raise ValueError("Webhook URL must not target a private or reserved IP address")

    default_port = 443 if scheme == "https" else 80
    netloc_host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = netloc_host if port in (None, default_port) else f"{netloc_host}:{port}"
    path = parsed.path or "/"
    normalized = urlunsplit((scheme, netloc, path, parsed.query, ""))
    if len(normalized) > _MAX_WEBHOOK_URL_LENGTH:
        raise ValueError("Webhook URL is too long")
    return normalized


def resolve_public_webhook_addresses(url: str) -> tuple[str, tuple[str, ...]]:
    normalized = normalize_webhook_url(url)
    parsed = urlsplit(normalized)
    hostname = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    try:
        records = socket.getaddrinfo(
            hostname,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise ValueError("Webhook hostname could not be resolved") from exc

    addresses: set[str] = set()
    for record in records:
        raw_address = record[4][0]
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError as exc:
            raise ValueError("Webhook hostname resolved to an invalid address") from exc
        if not _is_public_address(address):
            raise ValueError("Webhook hostname resolves to a private or reserved address")
        addresses.add(str(address))
        if len(addresses) > _MAX_RESOLVED_ADDRESSES:
            raise ValueError("Webhook hostname resolves to too many addresses")

    if not addresses:
        raise ValueError("Webhook hostname has no usable addresses")
    return normalized, tuple(sorted(addresses))
