from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Depends, Query, Response
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session, selectinload

from .admin import router as admin_router
from ..database import get_db
from ..models import (
    AuditLog,
    Customer,
    MoySkladConflict,
    MoySkladMappingRule,
    Order,
    Product,
)
from ..schemas import (
    AuditLogOut,
    MoySkladConflictOut,
    MoySkladMappingRuleOut,
    OrderOut,
    ProductOut,
)
from ..security import get_current_admin
from ..services.rbac import require_permission

DEFAULT_LIST_LIMIT = 100
MAX_LIST_LIMIT = 500
MAX_LIST_OFFSET = 10_000_000


def _pagination_headers(
    response: Response,
    *,
    limit: int,
    offset: int,
    has_more: bool,
) -> None:
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Page-Limit"] = str(limit)
    response.headers["X-Page-Offset"] = str(offset)
    response.headers["X-Has-More"] = "true" if has_more else "false"


def _bounded(rows: list[Any], *, limit: int) -> tuple[list[Any], bool]:
    return rows[:limit], len(rows) > limit


def bounded_products(
    response: Response,
    limit: int = Query(default=DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
    offset: int = Query(default=0, ge=0, le=MAX_LIST_OFFSET),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "products.read")
    rows = (
        db.query(Product)
        .options(selectinload(Product.images), selectinload(Product.variants))
        .order_by(Product.created_at.desc(), Product.id.desc())
        .offset(offset)
        .limit(limit + 1)
        .all()
    )
    items, has_more = _bounded(rows, limit=limit)
    _pagination_headers(response, limit=limit, offset=offset, has_more=has_more)
    return items


def bounded_orders(
    response: Response,
    limit: int = Query(default=DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
    offset: int = Query(default=0, ge=0, le=MAX_LIST_OFFSET),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "orders.read")
    rows = (
        db.query(Order)
        .options(selectinload(Order.items))
        .order_by(Order.created_at.desc(), Order.id.desc())
        .offset(offset)
        .limit(limit + 1)
        .all()
    )
    items, has_more = _bounded(rows, limit=limit)
    _pagination_headers(response, limit=limit, offset=offset, has_more=has_more)
    return items


def bounded_audit_logs(
    response: Response,
    limit: int = Query(default=200, ge=1, le=MAX_LIST_LIMIT),
    offset: int = Query(default=0, ge=0, le=MAX_LIST_OFFSET),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "audit.read")
    rows = (
        db.query(AuditLog)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .offset(offset)
        .limit(limit + 1)
        .all()
    )
    items, has_more = _bounded(rows, limit=limit)
    _pagination_headers(response, limit=limit, offset=offset, has_more=has_more)
    return items


def bounded_customers(
    response: Response,
    limit: int = Query(default=DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
    offset: int = Query(default=0, ge=0, le=MAX_LIST_OFFSET),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "customers.read")
    rows = (
        db.query(Customer)
        .order_by(Customer.created_at.desc(), Customer.id.desc())
        .offset(offset)
        .limit(limit + 1)
        .all()
    )
    rows, has_more = _bounded(rows, limit=limit)
    _pagination_headers(response, limit=limit, offset=offset, has_more=has_more)
    return [
        {
            "id": customer.id,
            "telegram_id": customer.telegram_id,
            "username": customer.username,
            "first_name": customer.first_name,
            "phone": customer.phone,
        }
        for customer in rows
    ]


def bounded_mapping_rules(
    response: Response,
    limit: int = Query(default=DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
    offset: int = Query(default=0, ge=0, le=MAX_LIST_OFFSET),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "products.read")
    rows = (
        db.query(MoySkladMappingRule)
        .order_by(MoySkladMappingRule.id.desc())
        .offset(offset)
        .limit(limit + 1)
        .all()
    )
    items, has_more = _bounded(rows, limit=limit)
    _pagination_headers(response, limit=limit, offset=offset, has_more=has_more)
    return items


def bounded_moysklad_conflicts(
    response: Response,
    limit: int = Query(default=200, ge=1, le=MAX_LIST_LIMIT),
    offset: int = Query(default=0, ge=0, le=MAX_LIST_OFFSET),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "products.read")
    rows = (
        db.query(MoySkladConflict)
        .order_by(MoySkladConflict.created_at.desc(), MoySkladConflict.id.desc())
        .offset(offset)
        .limit(limit + 1)
        .all()
    )
    items, has_more = _bounded(rows, limit=limit)
    _pagination_headers(response, limit=limit, offset=offset, has_more=has_more)
    return items


def _replace_get_route(
    path: str,
    endpoint: Callable[..., Any],
    *,
    response_model: Any = None,
    name: str,
) -> None:
    matching = [
        route
        for route in admin_router.routes
        if isinstance(route, APIRoute)
        and route.path == f"/admin{path}"
        and "GET" in route.methods
    ]
    guarded = [route for route in matching if route.endpoint is endpoint]
    legacy = [route for route in matching if route.endpoint is not endpoint]
    if len(guarded) == 1 and not legacy:
        return
    if guarded or len(legacy) != 1:
        raise RuntimeError(f"Expected one legacy admin GET route for {path}")

    admin_router.routes.remove(legacy[0])
    admin_router.add_api_route(
        path,
        endpoint,
        methods=["GET"],
        response_model=response_model,
        name=name,
    )


def install_admin_list_bounds() -> None:
    _replace_get_route(
        "/products",
        bounded_products,
        response_model=list[ProductOut],
        name="bounded_admin_products",
    )
    _replace_get_route(
        "/orders",
        bounded_orders,
        response_model=list[OrderOut],
        name="bounded_admin_orders",
    )
    _replace_get_route(
        "/audit-logs",
        bounded_audit_logs,
        response_model=list[AuditLogOut],
        name="bounded_admin_audit_logs",
    )
    _replace_get_route(
        "/customers",
        bounded_customers,
        name="bounded_admin_customers",
    )
    _replace_get_route(
        "/moysklad/mapping-rules",
        bounded_mapping_rules,
        response_model=list[MoySkladMappingRuleOut],
        name="bounded_admin_mapping_rules",
    )
    _replace_get_route(
        "/moysklad/conflicts",
        bounded_moysklad_conflicts,
        response_model=list[MoySkladConflictOut],
        name="bounded_admin_moysklad_conflicts",
    )


install_admin_list_bounds()
