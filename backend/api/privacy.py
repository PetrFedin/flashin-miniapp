import json

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ConsentRecord, Customer, PrivacyRequest
from ..schemas import ConsentIn, PrivacyRequestCreate, PrivacyRequestOut
from ..security import get_current_admin, get_current_customer
from ..services.audit import log_admin_action
from ..services.privacy import (
    ALLOWED_CONSENT_TYPES,
    OPEN_PRIVACY_REQUEST_STATUSES,
    anonymize_customer,
    build_customer_export,
    mark_privacy_processed,
    withdraw_optional_consents,
)
from ..services.rbac import require_permission

router = APIRouter(prefix="/privacy", tags=["privacy"])
_PRIVACY_REQUEST_TYPES = {"export", "delete", "consent_withdrawal"}


def _request_type(value: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized not in _PRIVACY_REQUEST_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported privacy request type")
    return normalized


@router.post("/consent")
def set_consent(
    payload: ConsentIn,
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    consent_type = (payload.consent_type or "").strip().lower()
    if consent_type not in ALLOWED_CONSENT_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported consent type")

    try:
        latest = (
            db.query(ConsentRecord)
            .filter(
                ConsentRecord.customer_id == customer.id,
                ConsentRecord.consent_type == consent_type,
            )
            .order_by(ConsentRecord.created_at.desc(), ConsentRecord.id.desc())
            .with_for_update()
            .first()
        )
        if latest and latest.granted == payload.granted:
            return {"ok": True, "idempotent": True}

        db.add(
            ConsentRecord(
                customer_id=customer.id,
                consent_type=consent_type,
                granted=payload.granted,
                source="telegram_mini_app",
            )
        )
        db.commit()
        return {"ok": True, "idempotent": False}
    except Exception:
        db.rollback()
        raise


@router.post("/requests", response_model=PrivacyRequestOut)
def create_privacy_request(
    payload: PrivacyRequestCreate,
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    request_type = _request_type(payload.request_type)
    try:
        existing = (
            db.query(PrivacyRequest)
            .filter(
                PrivacyRequest.customer_id == customer.id,
                PrivacyRequest.request_type == request_type,
                PrivacyRequest.status.in_(OPEN_PRIVACY_REQUEST_STATUSES),
            )
            .order_by(PrivacyRequest.created_at.desc(), PrivacyRequest.id.desc())
            .with_for_update()
            .first()
        )
        if existing:
            return existing

        request = PrivacyRequest(
            customer_id=customer.id,
            request_type=request_type,
            status="requested",
        )
        db.add(request)
        db.commit()
        db.refresh(request)
        return request
    except Exception:
        db.rollback()
        raise


@router.get("/requests", response_model=list[PrivacyRequestOut])
def my_privacy_requests(
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    return (
        db.query(PrivacyRequest)
        .filter(PrivacyRequest.customer_id == customer.id)
        .order_by(PrivacyRequest.created_at.desc())
        .all()
    )


@router.get("/export")
def export_my_data(
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    data = build_customer_export(db, customer)
    return Response(
        content=json.dumps(data, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="flashin_customer_{customer.id}_export.json"'
            ),
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/admin/requests", response_model=list[PrivacyRequestOut])
def admin_privacy_requests(
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "privacy.read")
    return db.query(PrivacyRequest).order_by(PrivacyRequest.created_at.desc()).all()


@router.post("/admin/requests/{request_id}/process")
def admin_process_privacy_request(
    request_id: int,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "privacy.write")
    try:
        request = (
            db.query(PrivacyRequest)
            .filter(PrivacyRequest.id == request_id)
            .with_for_update()
            .first()
        )
        if not request:
            raise HTTPException(status_code=404, detail="Privacy request not found")
        if request.status == "processed":
            return {"ok": True, "idempotent": True, "result": {}}
        if request.status not in OPEN_PRIVACY_REQUEST_STATUSES:
            raise HTTPException(status_code=409, detail="Privacy request cannot be processed")

        customer = (
            db.query(Customer)
            .filter(Customer.id == request.customer_id)
            .with_for_update()
            .first()
        )
        if not customer:
            raise HTTPException(status_code=409, detail="Privacy request customer is missing")

        request.status = "processing"
        result: dict = {}
        result_url = ""
        if request.request_type == "export":
            result_url = "/api/privacy/export"
            result = {"export_ready": True}
        elif request.request_type == "consent_withdrawal":
            result = {
                "consents_withdrawn": withdraw_optional_consents(
                    db,
                    customer.id,
                    source="privacy_request",
                )
            }
        elif request.request_type == "delete":
            result = anonymize_customer(db, customer)
        else:
            raise HTTPException(status_code=400, detail="Unsupported privacy request type")

        mark_privacy_processed(request, result_url=result_url)
        log_admin_action(
            db,
            admin,
            "privacy.request.process",
            "privacy_request",
            request.id,
            {
                "request_type": request.request_type,
                "customer_id": request.customer_id,
                "result": result,
            },
        )
        db.commit()
        return {"ok": True, "idempotent": False, "result": result}
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
