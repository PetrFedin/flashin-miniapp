from sqlalchemy.orm import Session
from .config import get_settings
from .models import AdminUser, Product, ProductImage, ProductVariant, PromoCode
from .security import hash_password


def bootstrap_admin(db: Session) -> None:
    settings = get_settings()
    existing = db.query(AdminUser).filter(AdminUser.email == settings.admin_email).first()
    if existing:
        return
    admin = AdminUser(
        email=settings.admin_email,
        password_hash=hash_password(settings.admin_password),
        role="owner",
        active=True,
    )
    db.add(admin)
    db.commit()


def seed_products(db: Session) -> None:
    if db.query(Product).first():
        return
    products = [
        {
            "sku": "FLASHIN-COAT-001",
            "title": "FLASHIN Wool Coat",
            "slug": "flashin-wool-coat",
            "price": 28000,
            "category": "Outerwear",
            "images": ["https://placehold.co/800x1000?text=FLASHIN+Coat"],
            "variants": [
                {"size": "S", "sku": "FLASHIN-COAT-001-S", "stock_qty": 2},
                {"size": "M", "sku": "FLASHIN-COAT-001-M", "stock_qty": 3},
            ],
        }
    ]
    for data in products:
        product = Product(
            sku=data["sku"],
            title=data["title"],
            slug=data["slug"],
            brand="FLASHIN",
            description="Seed product. Disable ENABLE_SEED in production.",
            price=data["price"],
            currency="RUB",
            category=data["category"],
            active=True,
        )
        db.add(product)
        db.flush()
        for idx, url in enumerate(data["images"]):
            db.add(ProductImage(product_id=product.id, url=url, sort_order=idx))
        for v in data["variants"]:
            db.add(ProductVariant(product_id=product.id, size=v["size"], sku=v["sku"], stock_qty=v["stock_qty"]))
    if not db.query(PromoCode).filter(PromoCode.code == "FLASH10").first():
        db.add(PromoCode(code="FLASH10", discount_type="percent", discount_value=10, min_amount=1000, max_uses=100, active=True))
    db.commit()
