from sqlalchemy.orm import Session
from ..models import MoySkladMappingRule, Product, ProductImage, ProductVariant


def normalize_attribute(db: Session, field: str, value: str, fallback: str = "") -> str:
    rule = (
        db.query(MoySkladMappingRule)
        .filter(
            MoySkladMappingRule.source_field == field,
            MoySkladMappingRule.source_value == str(value or ""),
            MoySkladMappingRule.active == True,
        )
        .first()
    )
    return rule.target_value if rule else (fallback or str(value or ""))


def apply_deep_product_mapping(db: Session, product: Product, row: dict) -> Product:
    product.category = normalize_attribute(db, "category", row.get("pathName", product.category), product.category)
    product.brand = normalize_attribute(db, "brand", row.get("brand", product.brand), product.brand)
    product.gender = normalize_attribute(db, "gender", row.get("gender", product.gender), product.gender)
    if row.get("salePrices"):
        try:
            product.price = float(row["salePrices"][0]["value"]) / 100
        except Exception:
            pass
    return product


def apply_deep_variant_mapping(db: Session, variant: ProductVariant, row: dict) -> ProductVariant:
    variant.size = normalize_attribute(db, "size", row.get("size", variant.size), variant.size)
    # color can be stored in SKU/title conventions later; this hook keeps mapping centralized.
    return variant


def safety_stock_available(stock_qty: int, reserved_qty: int, safety_stock: int = 1) -> int:
    return max(stock_qty - reserved_qty - safety_stock, 0)
