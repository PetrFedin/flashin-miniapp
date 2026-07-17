from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..enterprise_models import (
    BulkEditJob,
    MediaAssetMetadata,
    ProductVersion,
    PromotionRule,
    Supplier,
    SupplierDocument,
    WorkflowAction,
    WorkflowDefinition,
    WorkflowRequest,
)
from ..models import FeatureFlag, MediaAsset, Product
from ..security import get_current_admin
from ..services.audit import log_admin_action
from ..services.rbac import require_permission, require_superadmin

router = APIRouter(prefix="/enterprise", tags=["enterprise"])


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _snapshot(product: Product) -> dict[str, Any]:
    fields = (
        "sku", "title", "slug", "brand", "description", "price", "old_price",
        "currency", "category", "gender", "active", "is_drop", "is_rare",
    )
    return {field: getattr(product, field) for field in fields}


class ProductVersionIn(BaseModel):
    change_note: str = Field(default="", max_length=1000)
    snapshot: dict[str, Any] | None = None


class VersionActionIn(BaseModel):
    action: Literal["submit", "approve", "reject", "publish", "archive"]
    comment: str = Field(default="", max_length=1000)


@router.get("/pim/products/{product_id}/versions")
def product_versions(product_id: int, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "pim.read")
    return db.query(ProductVersion).filter(ProductVersion.product_id == product_id).order_by(ProductVersion.version_number.desc()).all()


@router.post("/pim/products/{product_id}/versions")
def create_product_version(payload: ProductVersionIn, product_id: int, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "pim.write")
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    number = (db.query(func.max(ProductVersion.version_number)).filter(ProductVersion.product_id == product_id).scalar() or 0) + 1
    row = ProductVersion(
        product_id=product_id,
        version_number=number,
        status="draft",
        snapshot_json=_dump(payload.snapshot or _snapshot(product)),
        change_note=payload.change_note,
        created_by=admin.id,
    )
    db.add(row)
    db.flush()
    log_admin_action(db, admin, "pim.version.create", "product_version", row.id, {"product_id": product_id, "version": number})
    db.commit()
    db.refresh(row)
    return row


@router.post("/pim/versions/{version_id}/action")
def product_version_action(payload: VersionActionIn, version_id: int, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    permission = "pim.approve" if payload.action in {"approve", "reject", "publish"} else "pim.write"
    require_permission(db, admin, permission)
    row = db.get(ProductVersion, version_id)
    if not row:
        raise HTTPException(status_code=404, detail="Product version not found")
    transitions = {
        "submit": ({"draft", "rejected"}, "review"),
        "approve": ({"review"}, "approved"),
        "reject": ({"review", "approved"}, "rejected"),
        "publish": ({"approved"}, "published"),
        "archive": ({"draft", "review", "approved", "published", "rejected"}, "archived"),
    }
    allowed, target = transitions[payload.action]
    if row.status not in allowed:
        raise HTTPException(status_code=409, detail=f"Cannot {payload.action} from status {row.status}")
    row.status = target
    row.updated_at = datetime.utcnow()
    if payload.comment:
        row.change_note = (row.change_note + "\n" + payload.comment).strip()
    if payload.action in {"approve", "reject", "publish"}:
        row.reviewed_by = admin.id
    if payload.action == "publish":
        product = db.get(Product, row.product_id)
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")
        for key, value in _load(row.snapshot_json, {}).items():
            if key in _snapshot(product):
                setattr(product, key, value)
        product.updated_at = datetime.utcnow()
        row.published_at = datetime.utcnow()
        db.query(ProductVersion).filter(
            ProductVersion.product_id == row.product_id,
            ProductVersion.id != row.id,
            ProductVersion.status == "published",
        ).update({"status": "archived", "archived_at": datetime.utcnow()}, synchronize_session=False)
    if payload.action == "archive":
        row.archived_at = datetime.utcnow()
    log_admin_action(db, admin, f"pim.version.{payload.action}", "product_version", row.id, {"status": target})
    db.commit()
    return {"ok": True, "id": row.id, "status": row.status}


class BulkEditIn(BaseModel):
    operation: Literal["archive", "restore", "set_category", "set_price", "increase_price_percent", "decrease_price_percent"]
    product_ids: list[int] = Field(min_length=1, max_length=5000)
    changes: dict[str, Any] = Field(default_factory=dict)


@router.post("/pim/bulk-jobs")
def bulk_edit_products(payload: BulkEditIn, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "pim.bulk")
    ids = list(dict.fromkeys(payload.product_ids))
    products = db.query(Product).filter(Product.id.in_(ids)).all()
    row = BulkEditJob(
        operation=payload.operation,
        filter_json=_dump({"product_ids": ids}),
        changes_json=_dump(payload.changes),
        total_count=len(ids),
        status="running",
        created_by=admin.id,
    )
    db.add(row)
    db.flush()
    found = {product.id for product in products}
    errors = [{"product_id": item, "error": "not_found"} for item in ids if item not in found]
    for product in products:
        try:
            if payload.operation == "archive":
                product.active = False
            elif payload.operation == "restore":
                product.active = True
            elif payload.operation == "set_category":
                product.category = str(payload.changes["category"]).strip()
            else:
                value = float(payload.changes["value"])
                old = float(product.price)
                product.old_price = old
                if payload.operation == "set_price":
                    product.price = max(value, 0)
                elif payload.operation == "increase_price_percent":
                    product.price = round(old * (1 + value / 100), 2)
                else:
                    product.price = round(max(old * (1 - value / 100), 0), 2)
            product.updated_at = datetime.utcnow()
            row.processed_count += 1
        except (KeyError, TypeError, ValueError) as exc:
            errors.append({"product_id": product.id, "error": str(exc)})
    row.failed_count = len(errors)
    row.error_json = _dump(errors)
    row.status = "completed_with_errors" if errors else "completed"
    row.completed_at = datetime.utcnow()
    log_admin_action(db, admin, "pim.bulk.execute", "bulk_edit_job", row.id, {"operation": payload.operation, "processed": row.processed_count, "failed": row.failed_count})
    db.commit()
    db.refresh(row)
    return row


class SupplierIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    legal_name: str = ""
    tax_id: str = ""
    contact_name: str = ""
    email: str = ""
    phone: str = ""
    notes: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class SupplierStatusIn(BaseModel):
    status: Literal["draft", "invited", "onboarding", "active", "suspended", "archived"]
    comment: str = ""


class SupplierDocumentIn(BaseModel):
    document_type: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=255)
    media_asset_id: int | None = None
    expires_at: datetime | None = None
    comment: str = ""


@router.get("/suppliers")
def suppliers(status: str | None = None, q: str | None = None, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "suppliers.read")
    query = db.query(Supplier)
    if status:
        query = query.filter(Supplier.status == status)
    if q:
        pattern = f"%{q.strip()}%"
        query = query.filter((Supplier.name.ilike(pattern)) | (Supplier.legal_name.ilike(pattern)) | (Supplier.tax_id.ilike(pattern)))
    return query.order_by(Supplier.updated_at.desc()).limit(500).all()


@router.post("/suppliers")
def create_supplier(payload: SupplierIn, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "suppliers.write")
    data = payload.model_dump(exclude={"metadata"})
    row = Supplier(**data, metadata_json=_dump(payload.metadata))
    db.add(row)
    db.flush()
    log_admin_action(db, admin, "supplier.create", "supplier", row.id, {"name": row.name})
    db.commit()
    db.refresh(row)
    return row


@router.patch("/suppliers/{supplier_id}/status")
def supplier_status(payload: SupplierStatusIn, supplier_id: int, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "suppliers.approve")
    row = db.get(Supplier, supplier_id)
    if not row:
        raise HTTPException(status_code=404, detail="Supplier not found")
    before = row.status
    row.status = payload.status
    row.updated_at = datetime.utcnow()
    if payload.comment:
        row.notes = (row.notes + "\n" + payload.comment).strip()
    log_admin_action(db, admin, "supplier.status", "supplier", row.id, {"before": before, "after": row.status})
    db.commit()
    return {"ok": True, "status": row.status}


@router.get("/suppliers/{supplier_id}/documents")
def supplier_documents(supplier_id: int, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "suppliers.read")
    return db.query(SupplierDocument).filter(SupplierDocument.supplier_id == supplier_id).order_by(SupplierDocument.created_at.desc()).all()


@router.post("/suppliers/{supplier_id}/documents")
def create_supplier_document(payload: SupplierDocumentIn, supplier_id: int, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "suppliers.write")
    if not db.get(Supplier, supplier_id):
        raise HTTPException(status_code=404, detail="Supplier not found")
    if payload.media_asset_id and not db.get(MediaAsset, payload.media_asset_id):
        raise HTTPException(status_code=404, detail="Media asset not found")
    row = SupplierDocument(supplier_id=supplier_id, **payload.model_dump())
    db.add(row)
    db.flush()
    log_admin_action(db, admin, "supplier.document.create", "supplier_document", row.id, {"supplier_id": supplier_id})
    db.commit()
    db.refresh(row)
    return row


class PromotionIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    code: str = Field(min_length=1, max_length=64)
    promotion_type: Literal["percent", "fixed", "buy_x_get_y", "category_percent"]
    priority: int = 100
    stackable: bool = False
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    conditions: dict[str, Any] = Field(default_factory=dict)
    actions: dict[str, Any] = Field(default_factory=dict)
    usage_limit: int = Field(default=0, ge=0)
    budget_limit: float = Field(default=0, ge=0)


class PromotionEvaluateIn(BaseModel):
    customer_segment: str = ""
    store_id: str = "default"
    items: list[dict[str, Any]] = Field(min_length=1)


def _discount(rule: PromotionRule, payload: PromotionEvaluateIn) -> float:
    conditions = _load(rule.conditions_json, {})
    actions = _load(rule.actions_json, {})
    segments = set(conditions.get("segments", []))
    if segments and payload.customer_segment not in segments:
        return 0
    categories = set(conditions.get("categories", []))
    eligible = [item for item in payload.items if not categories or item.get("category") in categories]
    total = sum(float(item.get("price", 0)) * int(item.get("quantity", 1)) for item in payload.items)
    eligible_total = sum(float(item.get("price", 0)) * int(item.get("quantity", 1)) for item in eligible)
    if total < float(conditions.get("min_total", 0)):
        return 0
    if rule.promotion_type in {"percent", "category_percent"}:
        return round(eligible_total * float(actions.get("percent", 0)) / 100, 2)
    if rule.promotion_type == "fixed":
        return round(min(float(actions.get("amount", 0)), total), 2)
    buy_qty = max(int(conditions.get("buy_quantity", 2)), 1)
    free_qty = max(int(actions.get("free_quantity", 1)), 1)
    prices: list[float] = []
    for item in eligible:
        prices.extend([float(item.get("price", 0))] * int(item.get("quantity", 1)))
    prices.sort()
    groups = len(prices) // (buy_qty + free_qty)
    return round(sum(prices[: groups * free_qty]), 2)


@router.get("/promotions")
def promotions(active: bool | None = None, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "promotions.read")
    query = db.query(PromotionRule)
    if active is not None:
        query = query.filter(PromotionRule.active == active)
    return query.order_by(PromotionRule.priority.asc(), PromotionRule.created_at.desc()).all()


@router.post("/promotions")
def create_promotion(payload: PromotionIn, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "promotions.write")
    code = payload.code.upper()
    if db.query(PromotionRule).filter(PromotionRule.code == code).first():
        raise HTTPException(status_code=409, detail="Promotion code already exists")
    data = payload.model_dump(exclude={"conditions", "actions", "code"})
    row = PromotionRule(**data, code=code, conditions_json=_dump(payload.conditions), actions_json=_dump(payload.actions), created_by=admin.id)
    db.add(row)
    db.flush()
    log_admin_action(db, admin, "promotion.create", "promotion", row.id, {"code": row.code})
    db.commit()
    db.refresh(row)
    return row


@router.patch("/promotions/{promotion_id}/active")
def promotion_active(promotion_id: int, enabled: bool = True, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "promotions.approve")
    row = db.get(PromotionRule, promotion_id)
    if not row:
        raise HTTPException(status_code=404, detail="Promotion not found")
    row.active = enabled
    row.updated_at = datetime.utcnow()
    log_admin_action(db, admin, "promotion.active", "promotion", row.id, {"enabled": enabled})
    db.commit()
    return {"ok": True, "active": row.active}


@router.post("/promotions/evaluate")
def evaluate_promotions(payload: PromotionEvaluateIn, db: Session = Depends(get_db)):
    now = datetime.utcnow()
    subtotal = sum(float(item.get("price", 0)) * int(item.get("quantity", 1)) for item in payload.items)
    applied = []
    discount = 0.0
    rows = db.query(PromotionRule).filter(PromotionRule.active.is_(True)).order_by(PromotionRule.priority.asc()).all()
    for row in rows:
        if (row.starts_at and row.starts_at > now) or (row.ends_at and row.ends_at < now):
            continue
        if row.usage_limit and row.usage_count >= row.usage_limit:
            continue
        value = _discount(row, payload)
        if row.budget_limit:
            value = min(value, max(row.budget_limit - row.spent_amount, 0))
        if value <= 0:
            continue
        applied.append({"id": row.id, "code": row.code, "name": row.name, "discount": value})
        discount += value
        if not row.stackable:
            break
    discount = min(discount, subtotal)
    return {"subtotal": round(subtotal, 2), "discount": round(discount, 2), "total": round(subtotal - discount, 2), "applied": applied}


class WorkflowDefinitionIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    entity_type: Literal["product", "price", "publication", "campaign", "promotion", "supplier"]
    steps: list[dict[str, Any]] = Field(min_length=1, max_length=20)


class WorkflowRequestIn(BaseModel):
    workflow_id: int
    entity_type: str
    entity_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    comment: str = ""


class WorkflowDecisionIn(BaseModel):
    action: Literal["approve", "reject", "cancel"]
    comment: str = ""


@router.post("/workflows/definitions")
def create_workflow(payload: WorkflowDefinitionIn, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "workflows.manage")
    if any(not step.get("role") and not step.get("permission") for step in payload.steps):
        raise HTTPException(status_code=422, detail="Every workflow step requires role or permission")
    row = WorkflowDefinition(name=payload.name, entity_type=payload.entity_type, steps_json=_dump(payload.steps))
    db.add(row)
    db.flush()
    log_admin_action(db, admin, "workflow.create", "workflow_definition", row.id, {"entity_type": row.entity_type})
    db.commit()
    db.refresh(row)
    return row


@router.get("/workflows/requests")
def workflow_requests(status: str | None = None, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "workflows.read")
    query = db.query(WorkflowRequest)
    if status:
        query = query.filter(WorkflowRequest.status == status)
    return query.order_by(WorkflowRequest.updated_at.desc()).limit(500).all()


@router.post("/workflows/requests")
def create_workflow_request(payload: WorkflowRequestIn, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "workflows.submit")
    definition = db.get(WorkflowDefinition, payload.workflow_id)
    if not definition or not definition.active:
        raise HTTPException(status_code=404, detail="Active workflow not found")
    steps = _load(definition.steps_json, [])
    if definition.entity_type != payload.entity_type:
        raise HTTPException(status_code=422, detail="Workflow entity type mismatch")
    row = WorkflowRequest(
        workflow_id=definition.id,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        status="pending",
        current_step=0,
        payload_json=_dump(payload.payload),
        requested_by=admin.id,
        assigned_role=str(steps[0].get("role", "")),
        comment=payload.comment,
    )
    db.add(row)
    db.flush()
    db.add(WorkflowAction(request_id=row.id, action="submit", step_index=0, actor_id=admin.id, comment=payload.comment))
    log_admin_action(db, admin, "workflow.request.create", "workflow_request", row.id, {"entity_type": row.entity_type, "entity_id": row.entity_id})
    db.commit()
    db.refresh(row)
    return row


@router.post("/workflows/requests/{request_id}/decision")
def workflow_decision(payload: WorkflowDecisionIn, request_id: int, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "workflows.approve")
    row = db.get(WorkflowRequest, request_id)
    if not row:
        raise HTTPException(status_code=404, detail="Workflow request not found")
    if row.status not in {"pending", "in_progress"}:
        raise HTTPException(status_code=409, detail=f"Workflow already {row.status}")
    definition = db.get(WorkflowDefinition, row.workflow_id)
    steps = _load(definition.steps_json if definition else "[]", [])
    if payload.action == "reject":
        row.status = "rejected"
    elif payload.action == "cancel":
        if row.requested_by != admin.id:
            require_superadmin(admin)
        row.status = "cancelled"
    else:
        next_step = row.current_step + 1
        if next_step >= len(steps):
            row.status = "approved"
            row.assigned_role = ""
        else:
            row.status = "in_progress"
            row.current_step = next_step
            row.assigned_role = str(steps[next_step].get("role", ""))
    row.comment = payload.comment or row.comment
    row.updated_at = datetime.utcnow()
    db.add(WorkflowAction(request_id=row.id, action=payload.action, step_index=row.current_step, actor_id=admin.id, comment=payload.comment))
    log_admin_action(db, admin, f"workflow.request.{payload.action}", "workflow_request", row.id, {"status": row.status})
    db.commit()
    return {"ok": True, "status": row.status, "current_step": row.current_step, "assigned_role": row.assigned_role}


@router.get("/workflows/requests/{request_id}/history")
def workflow_history(request_id: int, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "workflows.read")
    return db.query(WorkflowAction).filter(WorkflowAction.request_id == request_id).order_by(WorkflowAction.created_at.asc()).all()


class FeatureFlagIn(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9_.-]+$", min_length=2, max_length=120)
    description: str = ""
    enabled: bool = True


@router.get("/feature-flags")
def feature_flags(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "feature_flags.read")
    return db.query(FeatureFlag).order_by(FeatureFlag.key.asc()).all()


@router.put("/feature-flags/{key}")
def upsert_feature_flag(payload: FeatureFlagIn, key: str, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "feature_flags.write")
    if key != payload.key:
        raise HTTPException(status_code=422, detail="Path and payload keys must match")
    row = db.query(FeatureFlag).filter(FeatureFlag.key == key).first() or FeatureFlag(key=key)
    db.add(row)
    row.description = payload.description
    row.enabled = payload.enabled
    row.updated_at = datetime.utcnow()
    db.flush()
    log_admin_action(db, admin, "feature_flag.upsert", "feature_flag", row.id, {"key": key, "enabled": row.enabled})
    db.commit()
    db.refresh(row)
    return row


@router.get("/feature-flags/{key}/evaluate")
def evaluate_feature_flag(key: str, db: Session = Depends(get_db)):
    row = db.query(FeatureFlag).filter(FeatureFlag.key == key).first()
    if not row or not row.enabled:
        return {"key": key, "enabled": False, "reason": "disabled"}
    return {"key": key, "enabled": True, "reason": "enabled"}


class AssetMetadataIn(BaseModel):
    alt_text: str = ""
    tags: list[str] = Field(default_factory=list, max_length=100)
    ai_labels: list[str] = Field(default_factory=list, max_length=100)
    dominant_colors: list[str] = Field(default_factory=list, max_length=20)
    width: int = Field(default=0, ge=0)
    height: int = Field(default=0, ge=0)
    checksum: str = Field(default="", max_length=128)
    derivatives: dict[str, Any] = Field(default_factory=dict)


@router.put("/dam/assets/{asset_id}/metadata")
def asset_metadata(payload: AssetMetadataIn, asset_id: int, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "dam.write")
    if not db.get(MediaAsset, asset_id):
        raise HTTPException(status_code=404, detail="Media asset not found")
    row = db.query(MediaAssetMetadata).filter(MediaAssetMetadata.media_asset_id == asset_id).first() or MediaAssetMetadata(media_asset_id=asset_id)
    db.add(row)
    row.alt_text = payload.alt_text
    row.tags_json = _dump(sorted(set(payload.tags)))
    row.ai_labels_json = _dump(sorted(set(payload.ai_labels)))
    row.dominant_colors_json = _dump(payload.dominant_colors)
    row.width = payload.width
    row.height = payload.height
    row.checksum = payload.checksum
    row.derivatives_json = _dump(payload.derivatives)
    row.updated_at = datetime.utcnow()
    db.flush()
    log_admin_action(db, admin, "dam.metadata.upsert", "media_asset", asset_id, {"tags": len(payload.tags)})
    db.commit()
    db.refresh(row)
    return row


@router.get("/dam/assets/search")
def search_assets(q: str = "", tag: str = "", content_type: str = "", limit: int = Query(default=50, ge=1, le=200), admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "dam.read")
    query = db.query(MediaAsset, MediaAssetMetadata).outerjoin(MediaAssetMetadata, MediaAssetMetadata.media_asset_id == MediaAsset.id)
    if q:
        pattern = f"%{q.strip()}%"
        query = query.filter((MediaAsset.filename.ilike(pattern)) | (MediaAssetMetadata.alt_text.ilike(pattern)) | (MediaAssetMetadata.ai_labels_json.ilike(pattern)))
    if tag:
        query = query.filter(MediaAssetMetadata.tags_json.ilike(f'%"{tag}"%'))
    if content_type:
        query = query.filter(MediaAsset.content_type.ilike(f"{content_type}%"))
    rows = query.order_by(MediaAsset.created_at.desc()).limit(limit).all()
    return [{"asset": asset, "metadata": metadata} for asset, metadata in rows]
