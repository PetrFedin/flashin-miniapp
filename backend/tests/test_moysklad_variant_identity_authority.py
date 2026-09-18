import asyncio
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import MoySkladConflict, Product, ProductVariant
from backend.services import moysklad


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _settings():
    return SimpleNamespace(
        moysklad_sync_limit=100,
        moysklad_sale_price_type="",
        moysklad_default_currency="RUB",
        moysklad_size_attribute_names="Размер,Size",
        moysklad_color_attribute_names="Цвет,Color",
    )


def _product(
    *,
    provider_id="product-1",
    sku="STYLE-1",
    variants_count=2,
):
    return {
        "id": provider_id,
        "meta": {"type": "product"},
        "article": sku,
        "name": "Authority Jacket",
        "description": "Parent style",
        "pathName": "Outerwear",
        "variantsCount": variants_count,
        "salePrices": [{"value": 100000}],
    }


def _variant(
    *,
    provider_id,
    parent_id="product-1",
    sku,
    size,
    color="Black",
):
    return {
        "id": provider_id,
        "meta": {"type": "variant"},
        "product": {
            "meta": {
                "href": (
                    "https://api.moysklad.ru/api/remap/1.2/entity/product/"
                    f"{parent_id}"
                ),
                "type": "product",
            }
        },
        "article": sku,
        "name": f"Authority Jacket {size}",
        "attributes": [
            {"name": "Размер", "value": size},
            {"name": "Цвет", "value": color},
        ],
    }


def _sync(monkeypatch, db, rows):
    monkeypatch.setattr(moysklad, "get_settings", _settings)

    async def fake_fetch(*, limit, offset):
        assert limit == 100
        if offset:
            return {"rows": []}
        return {"rows": list(rows)}

    monkeypatch.setattr(moysklad, "fetch_assortment", fake_fetch)
    result = asyncio.run(moysklad.sync_assortment_to_catalog(db))
    assert result.status == "success", result.error
    return result


def test_two_provider_variants_share_one_parent_product_without_phantom_base_variant(
    monkeypatch,
):
    db = _db()
    rows = [
        _variant(provider_id="variant-m", sku="STYLE-1-M", size="M"),
        _product(),
        _variant(provider_id="variant-l", sku="STYLE-1-L", size="L"),
    ]

    _sync(monkeypatch, db, rows)

    products = db.query(Product).all()
    variants = db.query(ProductVariant).order_by(ProductVariant.sku).all()
    assert len(products) == 1
    assert products[0].moysklad_id == "product-1"
    assert products[0].sku == "STYLE-1"
    assert len(variants) == 2
    assert {row.moysklad_id for row in variants} == {"variant-m", "variant-l"}
    assert {row.size for row in variants} == {"M", "L"}
    assert {row.product_id for row in variants} == {products[0].id}
    assert db.query(ProductVariant).filter(
        ProductVariant.moysklad_id == "product-1"
    ).count() == 0


def test_repeated_sync_and_provider_sku_renames_preserve_local_identity(monkeypatch):
    db = _db()
    first_rows = [
        _product(),
        _variant(provider_id="variant-m", sku="STYLE-1-M", size="M"),
        _variant(provider_id="variant-l", sku="STYLE-1-L", size="L"),
    ]
    _sync(monkeypatch, db, first_rows)

    product = db.query(Product).filter(Product.moysklad_id == "product-1").one()
    variant = db.query(ProductVariant).filter(
        ProductVariant.moysklad_id == "variant-m"
    ).one()
    product_local_id = product.id
    variant_local_id = variant.id

    renamed_rows = [
        _product(sku="STYLE-1-RENAMED"),
        _variant(
            provider_id="variant-m",
            sku="STYLE-1-M-RENAMED",
            size="M",
        ),
        _variant(provider_id="variant-l", sku="STYLE-1-L", size="L"),
    ]
    _sync(monkeypatch, db, renamed_rows)
    _sync(monkeypatch, db, renamed_rows)

    product = db.query(Product).filter(Product.moysklad_id == "product-1").one()
    variant = db.query(ProductVariant).filter(
        ProductVariant.moysklad_id == "variant-m"
    ).one()
    assert product.id == product_local_id
    assert product.sku == "STYLE-1-RENAMED"
    assert variant.id == variant_local_id
    assert variant.sku == "STYLE-1-M-RENAMED"
    assert db.query(Product).count() == 1
    assert db.query(ProductVariant).count() == 2


def test_same_sku_from_different_provider_product_fails_closed(monkeypatch):
    db = _db()
    db.add(
        Product(
            sku="COLLISION",
            moysklad_id="product-original",
            title="Original",
            slug="original",
            price=1000,
        )
    )
    db.commit()

    _sync(
        monkeypatch,
        db,
        [_product(provider_id="product-other", sku="COLLISION", variants_count=0)],
    )

    assert db.query(Product).count() == 1
    conflict = db.query(MoySkladConflict).filter(
        MoySkladConflict.conflict_type == "product_sku_identity_collision",
        MoySkladConflict.status == "open",
    ).one()
    assert conflict.moysklad_id == "product-other"


def test_existing_variant_cannot_be_silently_reparented(monkeypatch):
    db = _db()
    first_rows = [
        _product(provider_id="product-a", sku="STYLE-A", variants_count=1),
        _variant(
            provider_id="variant-a",
            parent_id="product-a",
            sku="STYLE-A-M",
            size="M",
        ),
    ]
    _sync(monkeypatch, db, first_rows)
    original = db.query(ProductVariant).filter(
        ProductVariant.moysklad_id == "variant-a"
    ).one()
    original_product_id = original.product_id

    second_rows = [
        _product(provider_id="product-b", sku="STYLE-B", variants_count=1),
        _variant(
            provider_id="variant-a",
            parent_id="product-b",
            sku="STYLE-A-M",
            size="M",
        ),
    ]
    _sync(monkeypatch, db, second_rows)

    db.refresh(original)
    assert original.product_id == original_product_id
    conflict = db.query(MoySkladConflict).filter(
        MoySkladConflict.conflict_type == "variant_parent_identity_conflict",
        MoySkladConflict.status == "open",
    ).one()
    assert conflict.moysklad_id == "variant-a"


def test_variant_without_parent_identity_never_creates_phantom_product(monkeypatch):
    db = _db()
    row = _variant(
        provider_id="variant-orphan",
        parent_id="product-missing",
        sku="ORPHAN-M",
        size="M",
    )
    row.pop("product")

    _sync(monkeypatch, db, [row])

    assert db.query(Product).count() == 0
    assert db.query(ProductVariant).count() == 0
    conflict = db.query(MoySkladConflict).filter(
        MoySkladConflict.conflict_type == "variant_missing_parent_identity",
        MoySkladConflict.status == "open",
    ).one()
    assert conflict.moysklad_id == "variant-orphan"


def test_standalone_product_keeps_same_provider_identity_for_sellable_variant(
    monkeypatch,
):
    db = _db()

    _sync(
        monkeypatch,
        db,
        [_product(provider_id="standalone", sku="SINGLE", variants_count=0)],
    )

    product = db.query(Product).one()
    variant = db.query(ProductVariant).one()
    assert product.moysklad_id == "standalone"
    assert variant.moysklad_id == "standalone"
    assert variant.product_id == product.id
