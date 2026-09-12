import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..delivery_schemas import DeliveryBookingReconcileIn
from ..models import DeliveryProvider, DeliveryShipment, Order
from ..schemas import DeliveryProviderIn, DeliveryProviderOut, DeliveryShipmentOut
from ..security import get_current_admin
from ..services.audit import log_admin_action
from ..services.delivery_locking import lock_delivery_shipment_for_update
from ..services.delivery_provider_runtime import (
    DELIVERY_PROVIDER_MODES,
    DeliveryProviderConfigurationError,
    configured_delivery_provider_mode,
    delivery_booking_command,
    reconcile_delivery_booking,
    shipment_provider_mode,
)
from ..services.delivery_providers import ensure_ready_shipment, transition_shipment
from ..services.rbac import DELIVERY_PROVIDERS_WRITE_PERMISSION, require_permission

router = APIRouter(prefix="/delivery-providers", tags=["delivery-providers"])

_PROVIDER_CONFIG_MAX_BYTES = 8 * 1024
_SENSITIVE_PROVIDER_CONFIG_KEYS = {
    "access_key",
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "credential",
    "credentials",
    "password",
    "passwd",
    "private_key",
    "refresh_token",
    "secret",
    "signing_secret",
    "token",
}
_SENSITIVE_PROVIDER_CONFIG_SUFFIXES = (
    "_api_key",
    "_password",
    "_private_key",
    "_secret",
    "_token",
)


def _public_provider(provider: DeliveryProvider) -> dict:
    try:
        mode = configured_delivery_provider_mode(provider)
    except DeliveryProviderConfigurationError:
        mode = "invalid"
    return {
        "id": provider.id,
        "code": provider.code,
        "name": provider.name,
        "active": provider.active,
        # Secrets are forbidden in config_json; expose only the operational mode
        # so operators can distinguish disabled/manual/sandbox/live safely.
        "config_json": json.dumps({"mode": mode}, sort_keys=True),
    }


def _booking_status(db: Session, shipment: DeliveryShipment) -> dict[str, object]:
    try:
        mode = shipment_provider_mode(shipment)
    except DeliveryProviderConfigurationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    command = delivery_booking_command(db, shipment.id)
    if mode == "manual":
        status = "not_applicable"
    elif command is None:
        status = "missing"
    else:
        status = str(command.status)
    return {
        "shipment_id": int(shipment.id),
        "provider_code": str(shipment.provider_code),
        "provider_mode": mode,
        "status": status,
        "attempts": int(command.attempts) if command else 0,
        "external_id": str(command.external_id or "") if command else "",
        "requires_review": bool(command and command.status == "review_required"),
        "completed_at": command.completed_at.isoformat() if command and command.completed_at else None,
    }


def _normalize_config_key(value: object) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _sensitive_config_paths(value: object, path: str = "config_json") -> list[str]:
    matches: list[str] = []
    if isinstance(value, dict):
        for raw_key, nested in value.items():
            key = _normalize_config_key(raw_key)
            current = f"{path}.{key or '<empty>'}"
            if key in _SENSITIVE_PROVIDER_CONFIG_KEYS or key.endswith(_SENSITIVE_PROVIDER_CONFIG_SUFFIXES):
                matches.append(current)
            matches.extend(_sensitive_config_paths(nested, current))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            matches.extend(_sensitive_config_paths(nested, f"{path}[{index}]"))
    return matches


def _validated_provider_config(config: dict) -> str:
    sensitive_paths = _sensitive_config_paths(config)
    if sensitive_paths:
        raise HTTPException(
            status_code=400,
            detail=(
                "Delivery provider config_json must not contain credentials or secrets; "
                "use secret-managed provider configuration"
            ),
        )
    normalized = dict(config)
    mode = str(normalized.get("mode") or "manual").strip().lower()
    if mode not in DELIVERY_PROVIDER_MODES:
        raise HTTPException(
            status_code=400,
            detail="Delivery provider mode must be disabled, manual, sandbox or live",
        )
    normalized["mode"] = mode
    encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
    if len(encoded.encode("utf-8")) > _PROVIDER_CONFIG_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Delivery provider config_json is too large")
    return encoded


@router.get("", response_model=list[DeliveryProviderOut])
def providers(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "orders.read")
    rows = db.query(DeliveryProvider).order_by(DeliveryProvider.code).all()
    return [_public_provider(row) for row in rows]


@router.post("", response_model=DeliveryProviderOut)
def upsert_provider(
    payload: DeliveryProviderIn,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, DELIVERY_PROVIDERS_WRITE_PERMISSION)
    config_json = _validated_provider_config(dict(payload.config_json or {}))
    row = db.query(DeliveryProvider).filter(DeliveryProvider.code == payload.code).first()
    if not row:
        row = DeliveryProvider(code=payload.code)
        db.add(row)
    row.name = payload.name
    row.active = payload.active
    row.config_json = config_json
    db.flush()
    log_admin_action(
        db,
        admin,
        "delivery.provider.upsert",
        "delivery_provider",
        row.id,
        {
            "code": row.code,
            "name": row.name,
            "active": row.active,
            "provider_mode": configured_delivery_provider_mode(row),
            "config_changed": True,
        },
    )
    db.commit()
    db.refresh(row)
    return _public_provider(row)


@router.post("/orders/{order_id}/shipment", response_model=DeliveryShipmentOut)
def create_order_shipment(
    order_id: int,
    provider_code: str = "",
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "fulfillment.write")
    try:
        order = (
            db.query(Order)
            .filter(Order.id == order_id)
            .with_for_update()
            .first()
        )
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        try:
            shipment, created = ensure_ready_shipment(db, order, provider_code)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if created:
            log_admin_action(
                db,
                admin,
                "delivery.shipment.create",
                "delivery_shipment",
                shipment.id,
                {
                    "order_id": order.id,
                    "provider_code": shipment.provider_code,
                    "commercial_basis": "accepted_delivery_quote",
                },
            )
        db.commit()
        db.refresh(shipment)
        return shipment
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


@router.get("/shipments/{shipment_id}/booking")
def shipment_booking_status(
    shipment_id: int,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "orders.read")
    shipment = db.query(DeliveryShipment).filter(DeliveryShipment.id == shipment_id).first()
    if shipment is None:
        raise HTTPException(status_code=404, detail="Shipment not found")
    return _booking_status(db, shipment)


@router.post("/shipments/{shipment_id}/booking/reconcile")
def reconcile_shipment_booking(
    shipment_id: int,
    payload: DeliveryBookingReconcileIn,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, DELIVERY_PROVIDERS_WRITE_PERMISSION)
    try:
        shipment = (
            db.query(DeliveryShipment)
            .filter(DeliveryShipment.id == shipment_id)
            .with_for_update()
            .first()
        )
        if shipment is None:
            raise HTTPException(status_code=404, detail="Shipment not found")
        try:
            command = reconcile_delivery_booking(
                db,
                shipment_id=shipment.id,
                decision=payload.decision,
                external_id=payload.external_id,
            )
        except DeliveryProviderConfigurationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        log_admin_action(
            db,
            admin,
            "delivery.booking.reconcile",
            "delivery_shipment",
            shipment.id,
            {
                "provider_code": shipment.provider_code,
                "decision": payload.decision,
                "reason": payload.reason,
                "external_id_recorded": bool(command.external_id),
                "command_status": command.status,
            },
        )
        db.commit()
        db.refresh(shipment)
        return _booking_status(db, shipment)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


@router.patch("/shipments/{shipment_id}", response_model=DeliveryShipmentOut)
def patch_shipment(
    shipment_id: int,
    tracking_number: str = "",
    status: str = "shipped",
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "fulfillment.write")
    try:
        order, shipment = lock_delivery_shipment_for_update(db, shipment_id)
        previous_status = shipment.status
        try:
            transition_shipment(db, order, shipment, tracking_number, status)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        log_admin_action(
            db,
            admin,
            "delivery.shipment.update",
            "delivery_shipment",
            shipment.id,
            {
                "order_id": order.id,
                "from_status": previous_status,
                "status": shipment.status,
                "tracking_number": shipment.tracking_number,
                "order_status": order.status,
                "delivery_status": order.delivery_status,
            },
        )
        db.commit()
        db.refresh(shipment)
        return shipment
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


@router.get("/shipments", response_model=list[DeliveryShipmentOut])
def shipments(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "orders.read")
    return db.query(DeliveryShipment).order_by(DeliveryShipment.created_at.desc()).limit(300).all()
