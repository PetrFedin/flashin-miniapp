from sqlalchemy.orm import Session

from ..models import ProductVariant, StockReconciliationLog
from .moysklad_stock_authority import evaluate_moysklad_stock_snapshot


def reconcile_stock_rows(db: Session, external_rows: list[dict], apply: bool = False) -> int:
    """Compare external stock rows against local variants.

    external_rows format: [{"sku": "...", "stock_qty": 10}]
    Verified resalable warehouse receipts are authoritative while MoySklad is
    still exposing a stale lower snapshot; those rows are reported, not applied.
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
            if apply:
                decision = evaluate_moysklad_stock_snapshot(db, variant, external_stock)
                if decision.blocked:
                    # The authority helper already records an explicit open
                    # reconciliation plus a MoySklad conflict. Do not manufacture
                    # a second generic log that could hide the blocked reason.
                    count += 1
                    continue
                action = "applied"
                status = "resolved"
                target_stock = decision.target_stock
            else:
                action = "report"
                status = "open"
                target_stock = int(variant.stock_qty)

            db.add(StockReconciliationLog(
                variant_id=variant.id,
                sku=variant.sku,
                local_stock_qty=variant.stock_qty,
                external_stock_qty=external_stock,
                local_reserved_qty=variant.reserved_qty,
                action=action,
                status=status,
                message=f"Local stock {variant.stock_qty}, external stock {external_stock}",
            ))
            if apply:
                variant.stock_qty = target_stock
            count += 1
        elif apply:
            # Equality is meaningful evidence: it is the first safe observation
            # that can release a stale-return guard after warehouse inspection.
            evaluate_moysklad_stock_snapshot(db, variant, external_stock)
    db.commit()
    return count
