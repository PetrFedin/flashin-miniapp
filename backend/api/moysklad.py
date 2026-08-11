from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import MoySkladSyncLog, Order, ReturnRequest
from ..provider_models import ProviderCommand
from ..schemas import MoySkladSyncOut
from ..security import get_current_admin
from ..services.moysklad import sync_assortment_to_catalog
from ..services.rbac import require_permission

router = APIRouter(prefix="/moysklad", tags=["moysklad"])

_ORDER_COMMAND_TYPES = (
    "moysklad.customer_order.create",
    "moysklad.demand.create",
)
_RETURN_COMMAND_TYPE = "moysklad.sales_return.create"


def _serialize_outbound_command(command: ProviderCommand) -> dict[str, object]:
    """Return only non-secret operational evidence for one provider command."""

    return {
        "id": int(command.id),
        "provider": str(command.provider),
        "command_type": str(command.command_type),
        "aggregate_type": str(command.aggregate_type),
        "aggregate_id": str(command.aggregate_id),
        "status": str(command.status),
        "attempts": int(command.attempts),
        "external_id": str(command.external_id or ""),
        "created_at": command.created_at.isoformat() if command.created_at else None,
        "completed_at": command.completed_at.isoformat() if command.completed_at else None,
    }


@router.post("/sync", response_model=MoySkladSyncOut)
async def sync_moysklad(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.write")
    return await sync_assortment_to_catalog(db, sync_type="manual")


@router.get("/sync-logs", response_model=list[MoySkladSyncOut])
def sync_logs(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.read")
    return db.query(MoySkladSyncLog).order_by(MoySkladSyncLog.created_at.desc()).limit(50).all()


@router.get("/orders/{order_id}/outbound-evidence")
def order_outbound_evidence(
    order_id: int,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Read-only, sanitized proof of MoySklad outbound effects for one order.

    This endpoint intentionally excludes provider payloads, idempotency keys and
    error text. It exists for pilot operations and terminal E2E verification.
    """

    require_permission(db, admin, "orders.read")
    order = db.query(Order.id).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    return_ids = [
        int(row[0])
        for row in (
            db.query(ReturnRequest.id)
            .filter(ReturnRequest.order_id == order_id)
            .order_by(ReturnRequest.id.asc())
            .all()
        )
    ]

    order_commands = (
        db.query(ProviderCommand)
        .filter(
            ProviderCommand.provider == "moysklad",
            ProviderCommand.aggregate_type == "order",
            ProviderCommand.aggregate_id == str(order_id),
            ProviderCommand.command_type.in_(_ORDER_COMMAND_TYPES),
        )
        .all()
    )

    return_commands: list[ProviderCommand] = []
    if return_ids:
        return_commands = (
            db.query(ProviderCommand)
            .filter(
                ProviderCommand.provider == "moysklad",
                ProviderCommand.aggregate_type == "return",
                ProviderCommand.aggregate_id.in_([str(value) for value in return_ids]),
                ProviderCommand.command_type == _RETURN_COMMAND_TYPE,
            )
            .all()
        )

    commands = sorted(
        [*order_commands, *return_commands],
        key=lambda row: (row.created_at, row.id),
    )
    return {
        "order_id": order_id,
        "return_ids": return_ids,
        "commands": [_serialize_outbound_command(command) for command in commands],
    }
