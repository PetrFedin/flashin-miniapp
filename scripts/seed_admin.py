#!/usr/bin/env python3

from backend.config import get_settings
from backend.database import SessionLocal
from backend.models import AdminUser
from backend.security import hash_password


if __name__ == "__main__":
    settings = get_settings()
    email = settings.admin_email.strip().lower()
    password = settings.admin_password

    db = SessionLocal()
    try:
        user = db.query(AdminUser).filter(AdminUser.email == email).first()
        if user:
            print({"admin_exists": email})
        else:
            db.add(
                AdminUser(
                    email=email,
                    password_hash=hash_password(password),
                    role="owner",
                    active=True,
                )
            )
            db.commit()
            print({"admin_created": email})
    finally:
        db.close()
