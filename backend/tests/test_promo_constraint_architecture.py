from pathlib import Path

from backend.models import PromoCode


ROOT = Path(__file__).resolve().parents[2]


def test_promo_metadata_contains_definition_constraints():
    constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in PromoCode.__table__.constraints
        if constraint.name and hasattr(constraint, "sqltext")
    }

    assert "ck_promo_codes_discount_type" in constraints
    assert "discount_type" in constraints["ck_promo_codes_discount_type"]
    assert "ck_promo_codes_percent_bounded" in constraints
    assert "discount_value <= 100" in constraints["ck_promo_codes_percent_bounded"]


def test_promo_migration_repairs_data_before_constraints():
    source = (
        ROOT / "backend/alembic/versions/0017_promo_definition_constraints.py"
    ).read_text(encoding="utf-8")

    repair_type = source.index("discount_type = 'fixed'")
    repair_percent = source.index("discount_value = 100")
    create_type = source.index("op.create_check_constraint(")
    create_percent = source.rindex("op.create_check_constraint(")

    assert repair_type < create_type
    assert repair_percent < create_percent
    assert 'down_revision = "0016_one_active_cart"' in source


def test_canonical_promo_route_is_physical_not_runtime_surgery():
    main = (ROOT / "backend/main.py").read_text(encoding="utf-8")
    monolith = (ROOT / "backend/api/admin.py").read_text(encoding="utf-8")
    canonical = (ROOT / "backend/api/admin_promos.py").read_text(encoding="utf-8")

    assert '("/admin/promocodes", "POST")' not in main
    assert "_REMOVED_MONOLITH_ADMIN_ROUTES" not in main
    assert "admin_router.routes[:]" not in main
    assert "admin_promos_router" in main
    assert '@router.post("/promocodes")' not in monolith
    assert "def admin_create_promo(" not in monolith
    assert '@router.post("/promocodes")' in canonical
    assert "normalize_promo_definition" in canonical
