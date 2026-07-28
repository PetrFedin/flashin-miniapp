import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta
from urllib.parse import parse_qsl

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db
from .models import AdminUser, Customer

bearer = HTTPBearer(auto_error=False)

_TELEGRAM_MAX_AGE_SECONDS = 60 * 60 * 24
_TELEGRAM_CLOCK_SKEW_SECONDS = 5 * 60
_PASSWORD_SCHEME = "pbkdf2_sha256"
_PASSWORD_ITERATIONS = 310_000


def verify_telegram_init_data(init_data: str) -> dict:
    settings = get_settings()
    if not init_data:
        raise HTTPException(status_code=401, detail="Telegram initData is missing")

    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        raise HTTPException(status_code=401, detail="Malformed Telegram initData")

    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise HTTPException(status_code=401, detail="Duplicate Telegram initData fields")

    parsed = dict(pairs)
    received_hash = parsed.pop("hash", None)
    if not received_hash or len(received_hash) != 64:
        raise HTTPException(status_code=401, detail="Telegram hash is missing or invalid")
    try:
        bytes.fromhex(received_hash)
    except ValueError:
        raise HTTPException(status_code=401, detail="Telegram hash is invalid")

    raw_auth_date = parsed.get("auth_date")
    try:
        auth_date = int(raw_auth_date)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Telegram auth_date is missing or invalid")

    now = int(time.time())
    if auth_date <= 0 or auth_date > now + _TELEGRAM_CLOCK_SKEW_SECONDS:
        raise HTTPException(status_code=401, detail="Telegram auth_date is invalid")
    if now - auth_date > _TELEGRAM_MAX_AGE_SECONDS:
        raise HTTPException(status_code=401, detail="Telegram initData expired")

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(parsed.items()))
    secret_key = hmac.new(
        key=b"WebAppData",
        msg=settings.telegram_bot_token.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    calculated_hash = hmac.new(
        key=secret_key,
        msg=data_check_string.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash.lower()):
        raise HTTPException(status_code=401, detail="Invalid Telegram signature")
    return parsed


def create_access_token(customer_id: int) -> str:
    settings = get_settings()
    issued_at = datetime.utcnow()
    expire = issued_at + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": str(customer_id),
        "type": "customer",
        "iat": issued_at,
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def get_current_customer(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> Customer:
    settings = get_settings()
    if not credentials:
        raise HTTPException(status_code=401, detail="Bearer token required")
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
        token_type = payload.get("type")
        if token_type not in {None, "customer"}:
            raise JWTError("wrong token type")
        customer_id = int(payload.get("sub"))
        if customer_id <= 0:
            raise ValueError("invalid customer id")
    except (JWTError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")

    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=401, detail="Customer not found")
    return customer


def _legacy_password_hash(password: str) -> str:
    salt = hashlib.sha256(get_settings().jwt_secret.encode("utf-8")).hexdigest()[:16]
    return hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    if not isinstance(password, str) or not password:
        raise ValueError("Password must not be empty")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        _PASSWORD_ITERATIONS,
    ).hex()
    return f"{_PASSWORD_SCHEME}${_PASSWORD_ITERATIONS}${salt}${digest}"


def verify_password(password: str, password_hash: str) -> bool:
    if not isinstance(password, str) or not password or not password_hash:
        return False

    if password_hash.startswith(f"{_PASSWORD_SCHEME}$"):
        try:
            scheme, raw_iterations, salt, expected = password_hash.split("$", 3)
            iterations = int(raw_iterations)
            if scheme != _PASSWORD_SCHEME or iterations < 100_000 or iterations > 2_000_000:
                return False
            salt_bytes = bytes.fromhex(salt)
            if len(salt_bytes) < 16 or len(expected) != 64:
                return False
            calculated = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                salt_bytes,
                iterations,
            ).hex()
        except (TypeError, ValueError):
            return False
        return hmac.compare_digest(calculated, expected)

    return hmac.compare_digest(_legacy_password_hash(password), password_hash)


def password_needs_rehash(password_hash: str) -> bool:
    if not password_hash.startswith(f"{_PASSWORD_SCHEME}$"):
        return True
    try:
        _, raw_iterations, _, _ = password_hash.split("$", 3)
        return int(raw_iterations) < _PASSWORD_ITERATIONS
    except (TypeError, ValueError):
        return True


def create_admin_token(admin_id: int, role: str) -> str:
    settings = get_settings()
    issued_at = datetime.utcnow()
    expire = issued_at + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": f"admin:{admin_id}",
        "type": "admin",
        "role": role,
        "iat": issued_at,
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def get_current_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> AdminUser:
    settings = get_settings()
    if not credentials:
        raise HTTPException(status_code=401, detail="Admin bearer token required")
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
        token_type = payload.get("type")
        if token_type not in {None, "admin"}:
            raise JWTError("wrong token type")
        subject = str(payload.get("sub") or "")
        if not subject.startswith("admin:"):
            raise JWTError("not an admin token")
        admin_id = int(subject.split(":", 1)[1])
        if admin_id <= 0:
            raise ValueError("invalid admin id")
    except (JWTError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid admin token")

    admin = (
        db.query(AdminUser)
        .filter(AdminUser.id == admin_id, AdminUser.active.is_(True))
        .first()
    )
    if not admin:
        raise HTTPException(status_code=401, detail="Admin not found")
    return admin
