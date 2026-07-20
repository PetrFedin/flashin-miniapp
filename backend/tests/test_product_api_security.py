import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def _registered_routes(router, prefix: str = "") -> set[tuple[str, frozenset[str]]]:
    return {
        (f"{prefix}{route.path}", frozenset(route.methods or set()))
        for route in router.routes
    }


def test_public_products_router_only_registers_read_endpoints():
    from backend.api.products import router as products_router

    routes = _registered_routes(products_router, prefix="/api")

    assert ("/api/products", frozenset({"GET"})) in routes
    assert ("/api/products/{product_id}", frozenset({"GET"})) in routes
    assert ("/api/products/slug/{slug}", frozenset({"GET"})) in routes
    assert not any(
        path == "/api/products" and "POST" in methods
        for path, methods in routes
    )


@pytest.fixture()
def admin_api():
    from backend.api.admin import router as admin_router
    from backend.database import Base, get_db
    from backend.models import AdminUser
    from backend.security import create_admin_token, hash_password

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    with testing_session() as db:
        admin = AdminUser(
            email="catalog-admin@test.local",
            password_hash=hash_password("test-password"),
            role="owner",
            active=True,
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        token = create_admin_token(admin.id, admin.role)

    app = FastAPI()
    app.include_router(admin_router, prefix="/api")

    def override_get_db():
        with testing_session() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        yield client, token, testing_session

    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def _valid_product_payload() -> dict:
    return {
        "sku": "SECURE-PRODUCT-001",
        "title": "Secure Product",
        "slug": "secure-product",
        "price": 12990,
        "images": [
            "https://cdn.example.test/product-front.jpg",
            "https://cdn.example.test/product-back.jpg",
        ],
        "variants": [
            {
                "size": "M",
                "color": "black",
                "sku": "SECURE-PRODUCT-001-M",
                "stock_qty": 4,
            },
            {
                "size": "L",
                "color": "black",
                "sku": "SECURE-PRODUCT-001-L",
                "stock_qty": 2,
            },
        ],
    }


def test_admin_product_create_requires_authorization(admin_api):
    from backend.models import Product

    client, _, testing_session = admin_api

    response = client.post("/api/admin/products", json=_valid_product_payload())

    assert response.status_code in {401, 403}
    with testing_session() as db:
        assert db.query(Product).count() == 0


def test_authorized_admin_can_create_product_with_images_and_variants(admin_api):
    from backend.models import Product, ProductImage, ProductVariant

    client, token, testing_session = admin_api

    response = client.post(
        "/api/admin/products",
        headers={"Authorization": f"Bearer {token}"},
        json=_valid_product_payload(),
    )

    assert response.status_code == 200
    body = response.json()
    assert sorted(image["url"] for image in body["images"]) == [
        "https://cdn.example.test/product-back.jpg",
        "https://cdn.example.test/product-front.jpg",
    ]
    assert sorted(variant["sku"] for variant in body["variants"]) == [
        "SECURE-PRODUCT-001-L",
        "SECURE-PRODUCT-001-M",
    ]

    with testing_session() as db:
        product = db.query(Product).filter(Product.sku == "SECURE-PRODUCT-001").one()
        images = (
            db.query(ProductImage)
            .filter(ProductImage.product_id == product.id)
            .order_by(ProductImage.sort_order)
            .all()
        )
        variants = (
            db.query(ProductVariant)
            .filter(ProductVariant.product_id == product.id)
            .order_by(ProductVariant.id)
            .all()
        )
        assert [(image.url, image.sort_order) for image in images] == [
            ("https://cdn.example.test/product-front.jpg", 0),
            ("https://cdn.example.test/product-back.jpg", 1),
        ]
        assert [(variant.size, variant.color, variant.stock_qty) for variant in variants] == [
            ("M", "black", 4),
            ("L", "black", 2),
        ]


@pytest.mark.parametrize(
    "payload",
    [
        {"sku": "MISSING-TITLE", "slug": "missing-title", "price": 1000},
        {
            "sku": "INVALID-VARIANT",
            "title": "Invalid Variant",
            "slug": "invalid-variant",
            "price": 1000,
            "variants": [{"size": "M", "stock_qty": -1}],
        },
    ],
)
def test_admin_product_create_rejects_missing_or_invalid_fields(admin_api, payload):
    from backend.models import Product

    client, token, testing_session = admin_api

    response = client.post(
        "/api/admin/products",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )

    assert response.status_code == 422
    with testing_session() as db:
        assert db.query(Product).count() == 0
