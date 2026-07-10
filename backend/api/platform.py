import json
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import FeatureFlag, RemoteConfig, CmsPage, CmsBlock, BusinessEvent, AuditTrail
from ..schemas import (
    FeatureFlagIn, FeatureFlagOut, RemoteConfigIn, RemoteConfigOut,
    CmsPageIn, CmsPageOut, CmsBlockIn, CmsBlockOut,
    BusinessEventOut, AuditTrailOut,
)
from ..security import get_current_admin
from ..services.rbac import require_permission

router = APIRouter(prefix="/platform", tags=["platform"])


@router.get("/features")
def public_features(db: Session = Depends(get_db)):
    return {f.key: f.enabled for f in db.query(FeatureFlag).all()}


@router.post("/admin/features", response_model=FeatureFlagOut)
def upsert_feature(payload: FeatureFlagIn, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "orders.write")
    row = db.query(FeatureFlag).filter(FeatureFlag.key == payload.key).first()
    if not row:
        row = FeatureFlag(key=payload.key)
        db.add(row)
    row.enabled = payload.enabled
    row.description = payload.description
    db.commit()
    db.refresh(row)
    return row


@router.get("/remote-config")
def public_remote_config(db: Session = Depends(get_db)):
    return {r.key: json.loads(r.value_json or "{}") for r in db.query(RemoteConfig).all()}


@router.post("/admin/remote-config", response_model=RemoteConfigOut)
def upsert_remote_config(payload: RemoteConfigIn, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "orders.write")
    row = db.query(RemoteConfig).filter(RemoteConfig.key == payload.key).first()
    if not row:
        row = RemoteConfig(key=payload.key)
        db.add(row)
    row.value_json = json.dumps(payload.value_json, ensure_ascii=False)
    row.description = payload.description
    db.commit()
    db.refresh(row)
    return row


@router.get("/cms/pages/{slug}", response_model=CmsPageOut)
def get_page(slug: str, db: Session = Depends(get_db)):
    return db.query(CmsPage).filter(CmsPage.slug == slug, CmsPage.active == True).first()


@router.get("/cms/blocks/{page_slug}", response_model=list[CmsBlockOut])
def get_blocks(page_slug: str, db: Session = Depends(get_db)):
    return db.query(CmsBlock).filter(CmsBlock.page_slug == page_slug, CmsBlock.active == True).order_by(CmsBlock.sort_order).all()


@router.post("/admin/cms/pages", response_model=CmsPageOut)
def upsert_page(payload: CmsPageIn, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.write")
    row = db.query(CmsPage).filter(CmsPage.slug == payload.slug).first()
    if not row:
        row = CmsPage(slug=payload.slug)
        db.add(row)
    row.title = payload.title
    row.content_json = json.dumps(payload.content_json, ensure_ascii=False)
    row.active = payload.active
    db.commit()
    db.refresh(row)
    return row


@router.post("/admin/cms/blocks", response_model=CmsBlockOut)
def create_block(payload: CmsBlockIn, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.write")
    row = CmsBlock(
        page_slug=payload.page_slug,
        block_type=payload.block_type,
        title=payload.title,
        payload_json=json.dumps(payload.payload_json, ensure_ascii=False),
        sort_order=payload.sort_order,
        active=payload.active,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/admin/events", response_model=list[BusinessEventOut])
def list_events(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "orders.read")
    return db.query(BusinessEvent).order_by(BusinessEvent.created_at.desc()).limit(200).all()


@router.get("/admin/audit-trail", response_model=list[AuditTrailOut])
def list_audit_trail(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "orders.read")
    return db.query(AuditTrail).order_by(AuditTrail.created_at.desc()).limit(200).all()
