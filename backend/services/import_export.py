import csv
from pathlib import Path
from sqlalchemy.orm import Session
from ..config import get_settings
from ..models import Product, Order


def export_products_csv(db: Session) -> str:
    settings = get_settings()
    out_dir = Path(settings.import_export_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "products_export.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "sku", "title", "price", "currency", "category", "active"])
        for p in db.query(Product).order_by(Product.id).all():
            writer.writerow([p.id, p.sku, p.title, p.price, p.currency, p.category, p.active])
    return str(path)


def export_orders_csv(db: Session) -> str:
    settings = get_settings()
    out_dir = Path(settings.import_export_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "orders_export.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "status", "payment_status", "total_amount", "currency", "created_at"])
        for o in db.query(Order).order_by(Order.id).all():
            writer.writerow([o.id, o.status, o.payment_status, o.total_amount, o.currency, o.created_at.isoformat()])
    return str(path)
