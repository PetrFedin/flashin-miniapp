import base64
import binascii
import hashlib
import hmac
import ipaddress
import secrets
import struct
import time
from datetime import datetime, timedelta
from urllib.parse import quote

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.orm import Session

from ..admin_mfa_models import AdminTotpReplayState
from ..config import get_settings
from ..models import (
    AdminIpAllowlist,
    AdminLoginEvent,
    AdminPasswordReset,
    AdminSession,
    AdminTotpSecret,
    AdminUser,
)

_TOTP_ENCRYPTED_PREFIX = "enc:v1:"
_TOTP_NONCE_BYTES = 12


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def log_admin_login(
    db: Session,
    email: str,
    admin_id: int | None,
    success: bool,
    reason: str,
    ip: str = "",
    user_agent: str = "",
) -> None:
    db.add(
        AdminLoginEvent(
            admin_id=admin_id,
            email=(email or "").strip().lower()[:255],
            success=success,
            reason=(reason or "").strip()[:255],
            ip_address=(ip or "").strip()[:120],
            user_agent=(user_agent or "")[:2000],
        )
    )


def is_admin_ip_allowed(db: Session, ip: str) -> bool:
    rules = db.query(AdminIpAllowlist).filter(AdminIpAllowlist.active.is_(True)).all()
    if not rules:
        return True
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False

    for rule in rules:
        try:
            if address in ipaddress.ip_network(rule.cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def create_admin_session(
    db: Session,
    admin_id: int,
    token: str,
    ip: str = "",
    user_agent: str = "",
) -> AdminSession:
    token_hash = sha256(token)
    existing = (
        db.query(AdminSession)
        .filter(AdminSession.session_token_hash == token_hash)
        .with_for_update()
        .first()
    )
    if existing:
        existing.revoked = False
        existing.revoked_at = None
        existing.ip_address = (ip or "")[:120]
        existing.user_agent = (user_agent or "")[:2000]
        return existing

    session = AdminSession(
        admin_id=admin_id,
        session_token_hash=token_hash,
        ip_address=(ip or "")[:120],
        user_agent=(user_agent or "")[:2000],
        revoked=False,
    )
    db.add(session)
    return session


def is_admin_session_active(db: Session, admin_id: int, token: str) -> bool:
    token_hash = sha256(token)
    return (
        db.query(AdminSession.id)
        .filter(
            AdminSession.admin_id == admin_id,
            AdminSession.session_token_hash == token_hash,
            AdminSession.revoked.is_(False),
        )
        .first()
        is not None
    )


def revoke_admin_sessions(db: Session, admin_id: int) -> int:
    rows = (
        db.query(AdminSession)
        .filter(AdminSession.admin_id == admin_id, AdminSession.revoked.is_(False))
        .with_for_update()
        .all()
    )
    revoked_at = datetime.utcnow()
    for row in rows:
        row.revoked = True
        row.revoked_at = revoked_at
    return len(rows)


def create_password_reset(db: Session, admin: AdminUser) -> str:
    now = datetime.utcnow()
    active = (
        db.query(AdminPasswordReset)
        .filter(
            AdminPasswordReset.admin_id == admin.id,
            AdminPasswordReset.used.is_(False),
            AdminPasswordReset.expires_at > now,
        )
        .with_for_update()
        .all()
    )
    for row in active:
        row.used = True

    token = secrets.token_urlsafe(32)
    db.add(
        AdminPasswordReset(
            admin_id=admin.id,
            token_hash=sha256(token),
            expires_at=now + timedelta(hours=1),
        )
    )
    revoke_admin_sessions(db, admin.id)
    return token


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def normalize_totp_secret(secret: str) -> str:
    normalized = "".join((secret or "").strip().upper().split())
    if len(normalized) < 16 or len(normalized) > 128:
        raise ValueError("Invalid TOTP secret length")
    padding = "=" * ((8 - len(normalized) % 8) % 8)
    try:
        decoded = base64.b32decode(normalized + padding, casefold=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Invalid TOTP secret") from exc
    if len(decoded) < 10:
        raise ValueError("Invalid TOTP secret")
    return normalized


def totp_provisioning_uri(secret: str, email: str) -> str:
    normalized = normalize_totp_secret(secret)
    label = quote(f"FLASHIN:{(email or '').strip().lower()}", safe="")
    issuer = quote("FLASHIN", safe="")
    return (
        f"otpauth://totp/{label}?secret={normalized}&issuer={issuer}"
        "&algorithm=SHA1&digits=6&period=30"
    )


def _totp_aad(admin_id: int) -> bytes:
    if admin_id <= 0:
        raise ValueError("Invalid administrator id")
    return f"flashin:admin-totp:{admin_id}:v1".encode("utf-8")


def _totp_encryption_key() -> bytes:
    settings = get_settings()
    material = (settings.admin_totp_encryption_key or "").strip()
    if not material:
        if settings.app_env.strip().lower() == "production":
            raise ValueError("TOTP encryption key is not configured")
        material = settings.jwt_secret
    if len(material) < 8:
        raise ValueError("TOTP encryption key is too short")
    return hashlib.sha256(material.encode("utf-8")).digest()


def is_totp_secret_encrypted(secret: str) -> bool:
    return str(secret or "").startswith(_TOTP_ENCRYPTED_PREFIX)


def encrypt_totp_secret(admin_id: int, secret: str) -> str:
    normalized = normalize_totp_secret(secret)
    nonce = secrets.token_bytes(_TOTP_NONCE_BYTES)
    ciphertext = AESGCM(_totp_encryption_key()).encrypt(
        nonce,
        normalized.encode("ascii"),
        _totp_aad(admin_id),
    )
    encoded = base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")
    return f"{_TOTP_ENCRYPTED_PREFIX}{encoded}"


def decrypt_totp_secret(admin_id: int, stored_secret: str) -> str:
    raw = str(stored_secret or "")
    if not is_totp_secret_encrypted(raw):
        return normalize_totp_secret(raw)

    encoded = raw[len(_TOTP_ENCRYPTED_PREFIX) :]
    try:
        blob = base64.urlsafe_b64decode(encoded.encode("ascii"))
        if len(blob) <= _TOTP_NONCE_BYTES:
            raise ValueError("Encrypted TOTP secret is truncated")
        plaintext = AESGCM(_totp_encryption_key()).decrypt(
            blob[:_TOTP_NONCE_BYTES],
            _totp_aad(admin_id),
        )
        return normalize_totp_secret(plaintext.decode("ascii"))
    except (binascii.Error, UnicodeError, InvalidTag, ValueError) as exc:
        raise ValueError("TOTP secret cannot be decrypted") from exc


def match_totp_counter(
    secret: str,
    code: str,
    *,
    at_time: int | None = None,
    window: int = 1,
    step_seconds: int = 30,
) -> int | None:
    normalized_secret = normalize_totp_secret(secret)
    normalized_code = (code or "").strip()
    if len(normalized_code) != 6 or not normalized_code.isdigit():
        return None
    if window < 0 or window > 2 or step_seconds < 15:
        return None

    padding = "=" * ((8 - len(normalized_secret) % 8) % 8)
    key = base64.b32decode(normalized_secret + padding, casefold=True)
    current_time = int(time.time() if at_time is None else at_time)
    counter = current_time // step_seconds

    for offset in range(-window, window + 1):
        candidate_counter = counter + offset
        if candidate_counter < 0:
            continue
        digest = hmac.new(
            key,
            struct.pack(">Q", candidate_counter),
            hashlib.sha1,
        ).digest()
        index = digest[-1] & 0x0F
        value = struct.unpack(">I", digest[index : index + 4])[0] & 0x7FFFFFFF
        candidate = f"{value % 1_000_000:06d}"
        if hmac.compare_digest(candidate, normalized_code):
            return candidate_counter
    return None


def verify_totp(
    secret: str,
    code: str,
    *,
    at_time: int | None = None,
    window: int = 1,
    step_seconds: int = 30,
) -> bool:
    return (
        match_totp_counter(
            secret,
            code,
            at_time=at_time,
            window=window,
            step_seconds=step_seconds,
        )
        is not None
    )


def verify_stored_totp_counter(
    admin_id: int,
    stored_secret: str,
    code: str,
    *,
    at_time: int | None = None,
    window: int = 1,
) -> int | None:
    return match_totp_counter(
        decrypt_totp_secret(admin_id, stored_secret),
        code,
        at_time=at_time,
        window=window,
    )


def verify_stored_totp(
    admin_id: int,
    stored_secret: str,
    code: str,
    *,
    at_time: int | None = None,
    window: int = 1,
) -> bool:
    return (
        verify_stored_totp_counter(
            admin_id,
            stored_secret,
            code,
            at_time=at_time,
            window=window,
        )
        is not None
    )


def consume_totp_counter(db: Session, admin_id: int, counter: int) -> bool:
    row = (
        db.query(AdminTotpReplayState)
        .filter(AdminTotpReplayState.admin_id == admin_id)
        .with_for_update()
        .first()
    )
    if row and counter <= row.last_used_counter:
        return False
    if row:
        row.last_used_counter = counter
        row.updated_at = datetime.utcnow()
    else:
        db.add(
            AdminTotpReplayState(
                admin_id=admin_id,
                last_used_counter=counter,
            )
        )
    return True


def upgrade_totp_secret_encryption(row: AdminTotpSecret) -> bool:
    if is_totp_secret_encrypted(row.secret):
        return False
    row.secret = encrypt_totp_secret(row.admin_id, row.secret)
    return True


def set_totp_secret(
    db: Session,
    admin_id: int,
    secret: str,
    enabled: bool = False,
) -> AdminTotpSecret:
    encrypted_secret = encrypt_totp_secret(admin_id, secret)
    row = (
        db.query(AdminTotpSecret)
        .filter(AdminTotpSecret.admin_id == admin_id)
        .with_for_update()
        .first()
    )
    if not row:
        row = AdminTotpSecret(
            admin_id=admin_id,
            secret=encrypted_secret,
            enabled=enabled,
        )
        db.add(row)
    else:
        row.secret = encrypted_secret
        row.enabled = enabled

    replay_state = (
        db.query(AdminTotpReplayState)
        .filter(AdminTotpReplayState.admin_id == admin_id)
        .with_for_update()
        .first()
    )
    if replay_state:
        db.delete(replay_state)
    if enabled:
        revoke_admin_sessions(db, admin_id)
    return row
