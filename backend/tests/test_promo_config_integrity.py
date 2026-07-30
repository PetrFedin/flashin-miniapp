from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend import model_constraints  # noqa: F401
from backend.database import Base
from backend.models import PromoCode


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _promo(**overrides):
    values = {
        "code": "PROMO",
        "discount_type": "percent",
        "discount_value": 10,
        "min_amount": 0,
        "max_uses": 0,
        "used_count": 0,
        "active": True,
    }
    values.update(overrides)
    return PromoCode(**values)


def test_promo_is_normalized_and_quantized_before_insert():
    db = _session()
    promo = _promo(
        code="  summer-sale  ",
        discount_type=" PERCENT ",
        discount_value="12.34567",
        min_amount="1.235",
    )
    db.add(promo)
    db.commit()
    db.refresh(promo)

    assert promo.code == "SUMMER-SALE"
    assert promo.discount_type == "percent"
    assert Decimal(str(promo.discount_value)) == Decimal("12.3457")
    assert Decimal(str(promo.min_amount)) == Decimal("1.24")


@pytest.mark.parametrize(
    "overrides",
    [
        {"code": ""},
        {"code": "x" * 65},
        {"discount_type": "percentage"},
        {"discount_value": 0},
        {"discount_value": -1},
        {"discount_type": "percent", "discount_value": 100.0001},
        {"min_amount": -0.01},
        {"max_uses": -1},
        {"used_count": -1},
        {"max_uses": 2, "used_count": 3},
        {"max_uses": True},
    ],
)
def test_invalid_promo_configuration_is_rejected_before_insert(overrides):
    db = _session()
    db.add(_promo(**overrides))

    with pytest.raises(HTTPException) as caught:
        db.flush()
    assert caught.value.status_code == 400
    db.rollback()
    assert db.query(PromoCode).count() == 0


def test_invalid_update_is_rolled_back_without_corrupting_saved_promo():
    db = _session()
    promo = _promo()
    db.add(promo)
    db.commit()

    promo.discount_value = 101
    with pytest.raises(HTTPException):
        db.commit()
    db.rollback()

    saved = db.query(PromoCode).filter(PromoCode.id == promo.id).one()
    assert saved.discount_value == 10
    assert saved.discount_type == "percent"


def test_case_and_whitespace_variants_cannot_create_duplicate_codes():
    db = _session()
    db.add(_promo(code="SUMMER"))
    db.commit()

    db.add(_promo(code="  summer  "))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    assert db.query(PromoCode).count() == 1


@pytest.mark.parametrize(
    "values",
    [
        {"code": "DIRECT-TYPE", "discount_type": "other", "discount_value": 10},
        {"code": "DIRECT-ZERO", "discount_type": "fixed", "discount_value": 0},
        {"code": "DIRECT-PERCENT", "discount_type": "percent", "discount_value": 101},
        {"code": "   ", "discount_type": "fixed", "discount_value": 10},
        {"code": " lower ", "discount_type": "fixed", "discount_value": 10},
    ],
)
def test_database_constraints_reject_invalid_direct_sql(values):
    db = _session()
    statement = PromoCode.__table__.insert().values(
        min_amount=0,
        max_uses=0,
        used_count=0,
        active=True,
        **values,
    )
    with pytest.raises(IntegrityError):
        db.execute(statement)
    db.rollback()


def test_metadata_contains_all_new_promo_constraints():
    names = {constraint.name for constraint in PromoCode.__table__.constraints}
    assert {
        "ck_promo_codes_code_nonempty",
        "ck_promo_codes_code_normalized",
        "ck_promo_codes_discount_type_valid",
        "ck_promo_codes_discount_positive",
        "ck_promo_codes_percent_within_100",
        "ck_promo_codes_usage_within_limit",
    }.issubset(names)


def test_promo_migration_uses_collision_safe_two_phase_normalization():
    source = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0020_promo_code_integrity.py"
    ).read_text(encoding="utf-8")

    assert "CREATE TEMP TABLE promo_code_normalization_map" in source
    assert "SET code = '__promo_tmp_'" in source
    assert "WHERE final_code = candidate" in source
    assert "SET code = mapping.final_code" in source
