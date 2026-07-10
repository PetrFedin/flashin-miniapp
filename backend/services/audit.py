import json
from sqlalchemy.orm import Session
from ..config import get_settings
from ..models import AdminUser, AuditLog


def log_admin_action(
    db: Session,
    admin: AdminUser | None,
    action: str,
    entity_type: str = "",
    entity_id: str | int = "",
    payload: dict | None = None,
) -> None:
    settings = get_settings()
    if not settings.audit_log_enabled:
        return
    db.add(AuditLog(
        admin_id=admin.id if admin else None,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id or ""),
        payload=json.dumps(payload or {}, ensure_ascii=False),
    ))
