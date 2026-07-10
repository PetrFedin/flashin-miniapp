#!/usr/bin/env python3
import os
import hashlib
from backend.database import SessionLocal
from backend.models import AdminUser

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

if __name__ == "__main__":
    email = os.getenv("ADMIN_EMAIL", "admin@flashin.store")
    password = os.getenv("ADMIN_PASSWORD", "change-this-before-launch")
    db = SessionLocal()
    try:
        user = db.query(AdminUser).filter(AdminUser.email == email).first()
        if user:
            print({"admin_exists": email})
        else:
            db.add(AdminUser(email=email, password_hash=hash_password(password), role="owner", active=True))
            db.commit()
            print({"admin_created": email})
    finally:
        db.close()
