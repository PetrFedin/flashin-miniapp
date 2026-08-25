from pathlib import Path

from backend.catalog_models import (
    ProductExternalAvailability,
    ProductFeedback,
    ProductMerchandising,
    ProductVideo,
    ShowroomAppointment,
)
from backend.services.rbac import DEFAULT_PERMISSIONS

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api" / "catalog_merchandising.py"
MAIN = ROOT / "main.py"
RBAC = ROOT / "services" / "rbac.py"
MIGRATION = ROOT / "alembic" / "versions" / "0028_catalog_merchandising.py"


def test_catalog_tables_are_registered_with_expected_constraints():
    assert ProductMerchandising.__tablename__ == "product_merchandising"
    assert ProductVideo.__tablename__ == "product_videos"
    assert ProductExternalAvailability.__tablename__ == "product_external_availability"
    assert ProductFeedback.__tablename__ == "product_feedback"
    assert ShowroomAppointment.__tablename__ == "showroom_appointments"

    feedback_constraints = {item.name for item in ProductFeedback.__table__.constraints}
    assert "uq_product_feedback_customer" in feedback_constraints
    assert "ck_product_feedback_rating" in feedback_constraints
    showroom_constraints = {item.name for item in ShowroomAppointment.__table__.constraints}
    assert "ck_showroom_appointments_status" in showroom_constraints
    assert any(column.unique for column in ShowroomAppointment.__table__.columns if column.name == "active_slot_key")


def test_catalog_merchandising_api_owns_product_and_feedback_boundaries_only():
    source = API.read_text(encoding="utf-8")
    for route in (
        '@router.get("/products")',
        '@router.get("/products/{product_id}")',
        '@router.post("/products/{product_id}/feedback")',
        '@router.get("/admin/products")',
        '@router.post("/admin/products")',
        '@router.put("/admin/products/{product_id}")',
        '@router.put("/admin/products/{product_id}/recommendations")',
        '@router.patch("/admin/feedback/{feedback_id}")',
    ):
        assert route in source
    assert 'require_permission(db, admin, "products.write")' in source
    assert 'has_permission(db, admin, "inventory.write")' in source
    assert 'db.query(InventoryMovement.id)' in source
    assert 'transaction history and cannot be deleted' in source

    for legacy_showroom_route in (
        '@router.post("/showroom/appointments")',
        '@router.get("/showroom/appointments/me")',
        '@router.get("/admin/showroom/appointments")',
        '@router.patch("/admin/showroom/appointments/{appointment_id}")',
    ):
        assert legacy_showroom_route not in source


def test_catalog_public_serializer_does_not_expose_customer_identity_or_moysklad_ids():
    source = API.read_text(encoding="utf-8")
    public_function = source.split("def product_feedback", 1)[1].split("\ndef ", 1)[0]
    assert "customer_id" not in public_function
    assert "telegram_id" not in public_function

    assert 'if admin else {}' in source
    assert 'result["moysklad_id"] = product.moysklad_id' in source


def test_catalog_filters_sorting_share_and_safe_media_contract_are_present():
    source = API.read_text(encoding="utf-8")
    for token in (
        "material",
        "season",
        "availability_status",
        "badge",
        "size",
        "color",
        "min_price",
        "max_price",
        '"price_asc"',
        '"price_desc"',
        '"rating_desc"',
        '"grid_rank"',
        '"telegram_share_url"',
        'parsed.scheme not in {"http", "https"}',
    ):
        assert token in source


def test_runtime_and_alembic_register_catalog_models_and_single_revision_chain():
    main_source = MAIN.read_text(encoding="utf-8")
    assert "catalog_models as _catalog_models" in main_source
    assert "catalog_merchandising_router" in main_source
    migration_source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "0028_catalog_merchandising"' in migration_source
    assert 'down_revision = "0027_pilot_worker_heartbeats"' in migration_source


def test_default_roles_separate_showroom_customer_work_from_warehouse():
    source = RBAC.read_text(encoding="utf-8")
    assert '"showroom.read"' in source
    assert '"showroom.write"' in source
    assert {"showroom.read", "showroom.write"}.issubset(DEFAULT_PERMISSIONS["manager"])
    assert {"showroom.read", "showroom.write"}.issubset(DEFAULT_PERMISSIONS["support"])
    assert "showroom.read" not in DEFAULT_PERMISSIONS["warehouse"]
    assert "showroom.write" not in DEFAULT_PERMISSIONS["warehouse"]
