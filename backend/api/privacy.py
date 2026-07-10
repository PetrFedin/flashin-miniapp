import json
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import ConsentRecord, Customer, PrivacyRequest
from ..schemas import ConsentIn, PrivacyRequestCreate, PrivacyRequestOut
from ..security import get_current_admin, get_current_customer
from ..services.privacy import build_customer_export, mark_privacy_processed
from ..services.rbac import require_permission

router = APIRouter(prefix="/privacy", tags=["privacy"])


@router.post("/consent")
def set_consent(payload: ConsentIn, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    db.add(ConsentRecord(customer_id=customer.id, consent_type=payload.consent_type, granted=payload.granted))
    db.commit()
    return {"ok": True}


@router.post("/requests", response_model=PrivacyRequestOut)
def create_privacy_request(payload: PrivacyRequestCreate, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    if payload.request_type not in {"export", "delete", "consent_withdrawal"}:
        raise HTTPException(status_code=400, detail="Unsupported privacy request type")
    req = PrivacyRequest(customer_id=customer.id, request_type=payload.request_type, status="requested")
    db.add(req)
    db.commit()
    db.refresh(req)
    return req


@router.get("/requests", response_model=list[PrivacyRequestOut])
def my_privacy_requests(customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    return db.query(PrivacyRequest).filter(PrivacyRequest.customer_id == customer.id).order_by(PrivacyRequest.created_at.desc()).all()


@router.get("/export")
def export_my_data(customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    data = build_customer_export(db, customer)
    return Response(
        content=json.dumps(data, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=flashin_customer_export.json"},
    )


@router.get("/admin/requests", response_model=list[PrivacyRequestOut])
def admin_privacy_requests(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "customers.read")
    return db.query(PrivacyRequest).order_by(PrivacyRequest.created_at.desc()).all()


@router.post("/admin/requests/{request_id}/process")
def admin_process_privacy_request(request_id: int, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "customers.read")
    req = db.query(PrivacyRequest).filter(PrivacyRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Privacy request not found")
    mark_privacy_processed(req)
    db.commit()
    return {"ok": True}
