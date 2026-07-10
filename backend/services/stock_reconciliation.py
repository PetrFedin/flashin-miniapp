from sqlalchemy.orm import Session
from ..models import ProductVariant, StockReconciliationLog


def reconcile_stock_rows(db: Session, external_rows: list[dict], apply: bool = False) -> int:
    """Compare external stock rows against local variants.

    external_rows format: [{"sku": "...", "stock_qty": 10}]
    """
    count = 0
    for row in external_rows:
        sku = row.get("sku")
        if not sku:
            continue
        variant = db.query(ProductVariant).filter(ProductVariant.sku == sku).first()
        if not variant:
            continue
        external_stock = int(row.get("stock_qty") or 0)
        if variant.stock_qty != external_stock:
            action = "applied" if apply else "report"
            db.add(StockReconciliationLog(
                variant_id=variant.id,
                sku=variant.sku,
                local_stock_qty=variant.stock_qty,
                external_stock_qty=external_stock,
                local_reserved_qty=variant.reserved_qty,
                action=action,
                status="resolved" if apply else "open",
                message=f"Local stock {variant.stock_qty}, external stock {external_stock}",
            ))
            if apply:
                variant.stock_qty = max(external_stock, variant.reserved_qty)
            count += 1
    db.commit()
    return count
