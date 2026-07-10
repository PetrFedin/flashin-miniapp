import hashlib
import hmac
import time
from datetime import datetime, timedelta
from urllib.parse import parse_qsl

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db
from .models import Customer

bearer = HTTPBearer(auto_error=False)


def verify_telegram_init_data(init_data: str) -> dict:
    """Verify Telegram WebApp initData using bot token HMAC.

    This is mandatory. Do not trust initDataUnsafe from frontend.
    """
    settings = get_settings()
    if not init_data:
        raise HTTPException(status_code=401, detail="Telegram initData is missing")

    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="Telegram hash is missing")

    auth_date = int(parsed.get("auth_date", "0") or "0")
    if auth_date and time.time() - auth_date > 60 * 60 * 24:
        raise HTTPException(status_code=401, detail="Telegram initData expired")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(
        key=b"WebAppData",
        msg=settings.telegram_bot_token.encode(),
        digestmod=hashlib.sha256,
    ).digest()
    calculated_hash = hmac.new(
        key=secret_key,
        msg=data_check_string.encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise HTTPException(status_code=401, detail="Invalid Telegram signature")

    return parsed


def create_access_token(customer_id: int) -> str:
    settings = get_settings()
    expire = datetime.utcnow() + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": str(customer_id), "exp": expire}
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
        customer_id = int(payload.get("sub"))
    except (JWTError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=401, detail="Customer not found")
    return customer


def hash_password(password: str) -> str:
    salt = hashlib.sha256(get_settings().jwt_secret.encode()).hexdigest()[:16]
    return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()


def verify_password(password: str, password_hash: str) -> bool:
    return hmac.compare_digest(hash_password(password), password_hash)


def create_admin_token(admin_id: int, role: str) -> str:
    settings = get_settings()
    expire = datetime.utcnow() + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": f"admin:{admin_id}", "role": role, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


from .models import AdminUser


def get_current_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> AdminUser:
    settings = get_settings()
    if not credentials:
        raise HTTPException(status_code=401, detail="Admin bearer token required")
    try:
        payload = jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        sub = payload.get("sub", "")
        if not sub.startswith("admin:"):
            raise ValueError("not admin")
        admin_id = int(sub.split(":", 1)[1])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid admin token")
    admin = db.query(AdminUser).filter(AdminUser.id == admin_id, AdminUser.active == True).first()
    if not admin:
        raise HTTPException(status_code=401, detail="Admin not found")
    return admin
