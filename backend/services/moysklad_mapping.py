from sqlalchemy.orm import Session
from ..models import MoySkladConflict, MoySkladMappingRule


def apply_mapping(db: Session, source_field: str, source_value: str, fallback: str = "") -> str:
    rule = (
        db.query(MoySkladMappingRule)
        .filter(
            MoySkladMappingRule.source_field == source_field,
            MoySkladMappingRule.source_value == str(source_value),
            MoySkladMappingRule.active == True,
        )
        .first()
    )
    return rule.target_value if rule else (fallback or str(source_value or ""))


def log_conflict(db: Session, moysklad_id: str, sku: str, conflict_type: str, message: str) -> None:
    exists = (
        db.query(MoySkladConflict)
        .filter(MoySkladConflict.moysklad_id == moysklad_id, MoySkladConflict.conflict_type == conflict_type, MoySkladConflict.status == "open")
        .first()
    )
    if not exists:
        db.add(MoySkladConflict(moysklad_id=moysklad_id, sku=sku, conflict_type=conflict_type, message=message, status="open"))
