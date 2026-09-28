from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db, utcnow_naive
from ..delivery_models import DeliveryZoneRule
from ..delivery_schemas import DeliveryZoneAuthorityCreate, DeliveryZoneAuthorityUpdate
from ..models import DeliveryProvider, DeliveryZone
from ..security import get_current_admin
from ..services.audit import log_admin_action
from ..services.delivery_provider_runtime import delivery_provider_accepts_quotes
from ..services.rbac import DELIVERY_TARIFFS_WRITE_PERMISSION, require_permission

router = APIRouter(prefix="/delivery", tags=["delivery"])


def _provider_exists(db: Session, code: str) -> bool:
    normalized = str(code or "").strip().lower()
    if normalized == "pickup":
        return True
    return (
        db.query(DeliveryProvider.id)
        .filter(DeliveryProvider.code == normalized)
        .first()
        is not None
    )


def _provider_is_available(db: Session, code: str) -> bool:
    normalized = str(code or "").strip().lower()
    return delivery_provider_accepts_quotes(db, normalized)


def _validate_provider_for_tariff(
    db: Session,
    *,
    provider_code: str,
    tariff_active: bool,
) -> None:
    if not _provider_exists(db, provider_code):
        raise HTTPException(
            status_code=409,
            detail="Delivery provider must exist before it can be referenced by a tariff",
        )
    if tariff_active and not _provider_is_available(db, provider_code):
        raise HTTPException(
            status_code=409,
            detail="Active delivery tariff requires an available provider mode",
        )


def _zone_payload(zone: DeliveryZone, rule: DeliveryZoneRule | None) -> dict:
    return {
        "id": zone.id,
        "name": zone.name,
        "delivery_type": zone.delivery_type,
        "price": zone.price,
        "active": zone.active,
        "description": zone.description,
        "rule": None
        if rule is None
        else {
            "priority": rule.priority,
            "country_code": rule.country_code,
            "region": rule.region,
            "city": rule.city,
            "postal_prefix": rule.postal_prefix,
            "provider_code": rule.provider_code,
            "service_code": rule.service_code,
            "currency": rule.currency,
            "version": rule.version,
        },
    }


@router.get("/zones")
def list_delivery_zones(db: Session = Depends(get_db)):
    rows = (
        db.query(DeliveryZone, DeliveryZoneRule)
        .outerjoin(DeliveryZoneRule, DeliveryZoneRule.zone_id == DeliveryZone.id)
        .filter(DeliveryZone.active.is_(True))
        .order_by(DeliveryZone.delivery_type.asc(), DeliveryZoneRule.priority.asc(), DeliveryZone.id.asc())
        .all()
    )
    return [_zone_payload(zone, rule) for zone, rule in rows]


@router.post("/zones")
def create_delivery_zone(
    payload: DeliveryZoneAuthorityCreate,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, DELIVERY_TARIFFS_WRITE_PERMISSION)
    delivery_type = str(payload.delivery_type or "").strip().lower()
    if delivery_type != "courier":
        raise HTTPException(status_code=400, detail="Pickup is a built-in service; configured zones are courier-only")
    provider_code = str(payload.provider_code or "").strip().lower()
    service_code = str(payload.service_code or "").strip().lower()
    _validate_provider_for_tariff(
        db,
        provider_code=provider_code,
        tariff_active=bool(payload.active),
    )

    try:
        zone = DeliveryZone(
            name=payload.name.strip(),
            delivery_type=delivery_type,
            price=payload.price,
            active=payload.active,
            description=payload.description.strip(),
        )
        db.add(zone)
        db.flush()
        rule = DeliveryZoneRule(
            zone_id=zone.id,
            delivery_type=delivery_type,
            priority=payload.priority,
            country_code=payload.country_code,
            region=payload.region.strip(),
            city=payload.city.strip(),
            postal_prefix=payload.postal_prefix.strip(),
            provider_code=provider_code,
            service_code=service_code,
            currency=payload.currency,
            version=1,
        )
        db.add(rule)
        db.flush()
        log_admin_action(
            db,
            admin,
            "delivery.tariff.create",
            "delivery_zone",
            zone.id,
            _zone_payload(zone, rule),
        )
        db.commit()
        db.refresh(zone)
        db.refresh(rule)
        return _zone_payload(zone, rule)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Delivery zone name or delivery priority conflicts with an existing tariff",
        ) from exc
    except Exception:
        db.rollback()
        raise


@router.patch("/zones/{zone_id}")
def update_delivery_zone(
    zone_id: int,
    payload: DeliveryZoneAuthorityUpdate,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, DELIVERY_TARIFFS_WRITE_PERMISSION)
    try:
        zone = (
            db.query(DeliveryZone)
            .filter(DeliveryZone.id == zone_id)
            .with_for_update()
            .first()
        )
        if zone is None:
            raise HTTPException(status_code=404, detail="Delivery zone not found")
        rule = (
            db.query(DeliveryZoneRule)
            .filter(DeliveryZoneRule.zone_id == zone.id)
            .with_for_update()
            .first()
        )
        if rule is None:
            raise HTTPException(status_code=409, detail="Legacy delivery zone has no authoritative rule; recreate or migrate it")

        changes = payload.model_dump(exclude_unset=True)
        provider_code = str(changes.get("provider_code", rule.provider_code)).strip().lower()
        target_active = bool(changes.get("active", zone.active))
        _validate_provider_for_tariff(
            db,
            provider_code=provider_code,
            tariff_active=target_active,
        )

        before = _zone_payload(zone, rule)
        if "name" in changes:
            zone.name = str(changes["name"]).strip()
        if "price" in changes:
            zone.price = changes["price"]
        if "active" in changes:
            zone.active = bool(changes["active"])
        if "description" in changes:
            zone.description = str(changes["description"]).strip()
        for field in ("priority", "country_code", "region", "city", "postal_prefix", "currency"):
            if field in changes:
                setattr(rule, field, changes[field] if field in {"priority", "country_code", "currency"} else str(changes[field]).strip())
        if "provider_code" in changes:
            rule.provider_code = provider_code
        if "service_code" in changes:
            rule.service_code = str(changes["service_code"]).strip().lower()
        if changes:
            rule.version = int(rule.version or 0) + 1
            rule.updated_at = utcnow_naive()

        db.flush()
        after = _zone_payload(zone, rule)
        log_admin_action(
            db,
            admin,
            "delivery.tariff.update",
            "delivery_zone",
            zone.id,
            {"before": before, "after": after},
        )
        db.commit()
        return after
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Delivery priority or zone name conflicts with another tariff",
        ) from exc
    except Exception:
        db.rollback()
        raise
