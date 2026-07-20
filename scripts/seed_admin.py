#!/usr/bin/env python3
import os
from backend.database import SessionLocal
from backend.models import AdminUser
from backend.security import hash_password

if __name__ == "__main__":
    email = os.getenv("ADMIN_EMAIL", "admin@flashin.store")
    password = os.getenv("ADMIN_PASSWORD", "change-this-before-launch")
    db = SessionLocal()
    try:
        user = db.query(AdminUser).filter(AdminUser.email == email).first()
        if user:
            user.password_hash = hash_password(password)
            user.active = True
            db.commit()
            print({"admin_updated": email})
        else:
            db.add(AdminUser(email=email, password_hash=hash_password(password), role="owner", active=True))
            db.commit()
            print({"admin_created": email})
    finally:
        db.close()
