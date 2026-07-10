from sqlalchemy.orm import Session
from ..models import MoySkladSkuMatch, ProductVariant


def suggest_sku_matches(db: Session, external_rows: list[dict]) -> int:
    count = 0
    for row in external_rows:
        external_sku = row.get("sku") or row.get("article") or row.get("code")
        ms_id = row.get("id", "")
        if not external_sku:
            continue
        variant = db.query(ProductVariant).filter(ProductVariant.sku == external_sku).first()
        if not variant:
            continue
        existing = db.query(MoySkladSkuMatch).filter(MoySkladSkuMatch.local_variant_id == variant.id, MoySkladSkuMatch.moysklad_id == ms_id).first()
        if existing:
            continue
        db.add(MoySkladSkuMatch(local_variant_id=variant.id, moysklad_id=ms_id, external_sku=external_sku, confidence=1.0, confirmed=False))
        count += 1
    db.commit()
    return count


def confirm_sku_match(match: MoySkladSkuMatch) -> None:
    match.confirmed = True
