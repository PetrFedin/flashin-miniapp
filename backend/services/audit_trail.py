import json
from sqlalchemy.orm import Session
from ..models import AdminUser, AuditTrail


def log_audit_trail(
    db: Session,
    admin: AdminUser | None,
    action: str,
    entity_type: str = "",
    entity_id: str | int = "",
    before: dict | None = None,
    after: dict | None = None,
    ip_address: str = "",
    user_agent: str = "",
) -> None:
    db.add(AuditTrail(
        admin_id=admin.id if admin else None,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id or ""),
        before_json=json.dumps(before or {}, ensure_ascii=False),
        after_json=json.dumps(after or {}, ensure_ascii=False),
        ip_address=ip_address,
        user_agent=user_agent,
    ))
