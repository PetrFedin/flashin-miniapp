import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import enterprise_models, models, telegram_commerce_models
from backend.api import enterprise, telegram_webhook
from backend.database import Base
from backend.enterprise_models import WorkflowDefinition, WorkflowRequest
from backend.models import AdminRolePermission, AdminUser, Customer
from backend.telegram_commerce_models import TelegramOffer, TelegramPurchase


def normalize_predicate(expression, dialect):
    compiled = str(expression.compile(dialect=dialect))
    normalized = []
    quote = None
    pending_space = False
    index = 0
    while index < len(compiled):
        character = compiled[index]
        if quote is not None:
            normalized.append(character)
            if character == quote:
                if index + 1 < len(compiled) and compiled[index + 1] == quote:
                    normalized.append(compiled[index + 1])
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if character in {"'", '"'}:
            if pending_space and normalized:
                normalized.append(" ")
            pending_space = False
            quote = character
            normalized.append(character)
        elif character.isspace():
            pending_space = True
        else:
            if pending_space and normalized:
                normalized.append(" ")
            pending_space = False
            normalized.append(character)
        index += 1

    sql = "".join(normalized).strip()
    while sql.startswith("(") and sql.endswith(")"):
        depth = 0
        quote = None
        outer_parentheses_wrap_all = True
        index = 0
        while index < len(sql):
            character = sql[index]
            if quote is not None:
                if character == quote:
                    if index + 1 < len(sql) and sql[index + 1] == quote:
                        index += 2
                        continue
                    quote = None
            elif character in {"'", '"'}:
                quote = character
            elif character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0 and index != len(sql) - 1:
                    outer_parentheses_wrap_all = False
                    break
            index += 1
        if not outer_parentheses_wrap_all or depth != 0 or quote is not None:
            break
        sql = sql[1:-1].strip()
    return sql


def test_normalize_predicate_preserves_sql_literal_case():
    upper = normalize_predicate(text("kind   =   'A'"), sqlite.dialect())
    lower = normalize_predicate(text("kind = 'a'"), sqlite.dialect())

    assert upper == "kind = 'A'"
    assert lower == "kind = 'a'"
    assert upper != lower


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def _purchase_records(db_session):
    customer = Customer(telegram_id="review-customer")
    offer = TelegramOffer(
        code="review-offer",
        title="Review offer",
        offer_type="drop",
        stars_amount=125,
    )
    db_session.add_all([customer, offer])
    db_session.flush()
    return customer, offer


def test_pre_checkout_accepts_invoice_created_purchase(db_session, monkeypatch):
    customer, offer = _purchase_records(db_session)
    purchase = TelegramPurchase(
        customer_id=customer.id,
        offer_id=offer.id,
        invoice_payload="invoice-created-payload",
        stars_amount=offer.stars_amount,
    )
    db_session.add(purchase)
    db_session.commit()
    assert purchase.status == "invoice_created"

    bot_calls = []
    monkeypatch.setattr(telegram_webhook, "_validate_secret", lambda value: None)
    monkeypatch.setattr(
        telegram_webhook,
        "_bot_api",
        lambda method, payload: bot_calls.append((method, payload)),
    )

    result = telegram_webhook.telegram_webhook(
        {
            "pre_checkout_query": {
                "id": "pre-checkout-1",
                "invoice_payload": purchase.invoice_payload,
                "currency": "XTR",
                "total_amount": purchase.stars_amount,
            }
        },
        x_telegram_bot_api_secret_token="test-secret",
        db=db_session,
    )

    assert result["accepted"] is True
    assert bot_calls == [
        (
            "answerPreCheckoutQuery",
            {"pre_checkout_query_id": "pre-checkout-1", "ok": True},
        )
    ]


def test_multiple_unpaid_purchases_allow_null_charge_ids_but_paid_ids_are_unique(db_session):
    customer, offer = _purchase_records(db_session)
    purchases = [
        TelegramPurchase(
            customer_id=customer.id,
            offer_id=offer.id,
            invoice_payload=f"invoice-{number}",
            stars_amount=offer.stars_amount,
        )
        for number in (1, 2)
    ]
    db_session.add_all(purchases)
    db_session.commit()

    assert [purchase.telegram_payment_charge_id for purchase in purchases] == [None, None]

    purchases[0].telegram_payment_charge_id = "unique-charge"
    purchases[1].telegram_payment_charge_id = "unique-charge"
    with pytest.raises(IntegrityError):
        db_session.commit()


def _workflow_request(db_session, steps):
    definition = WorkflowDefinition(
        name="Review workflow",
        entity_type="product",
        steps_json=enterprise._dump(steps),
    )
    db_session.add(definition)
    db_session.flush()
    request = WorkflowRequest(
        workflow_id=definition.id,
        entity_type="product",
        entity_id="42",
        status="pending",
        current_step=0,
    )
    db_session.add(request)
    db_session.commit()
    return request


def _admin_with_permissions(db_session, role, *permissions):
    admin = AdminUser(
        email=f"{role}-{len(db_session.new)}@test.local",
        password_hash="test",
        role=role,
    )
    db_session.add(admin)
    db_session.flush()
    db_session.add_all(
        AdminRolePermission(role=role, permission=permission)
        for permission in permissions
    )
    db_session.commit()
    return admin


def test_workflow_decision_enforces_current_step_role(db_session):
    request = _workflow_request(db_session, [{"role": "finance"}])
    global_approver = _admin_with_permissions(db_session, "reviewer", "workflows.approve")

    with pytest.raises(HTTPException) as exc_info:
        enterprise.workflow_decision(
            enterprise.WorkflowDecisionIn(action="approve"),
            request.id,
            admin=global_approver,
            db=db_session,
        )
    assert exc_info.value.status_code == 403

    finance_approver = _admin_with_permissions(db_session, "finance", "workflows.approve")
    result = enterprise.workflow_decision(
        enterprise.WorkflowDecisionIn(action="approve"),
        request.id,
        admin=finance_approver,
        db=db_session,
    )
    assert result["status"] == "approved"


def test_workflow_decision_enforces_current_step_permission(db_session):
    request = _workflow_request(db_session, [{"permission": "finance.approve"}])
    admin = _admin_with_permissions(db_session, "finance", "workflows.approve")

    with pytest.raises(HTTPException) as exc_info:
        enterprise.workflow_decision(
            enterprise.WorkflowDecisionIn(action="reject"),
            request.id,
            admin=admin,
            db=db_session,
        )
    assert exc_info.value.status_code == 403

    db_session.add(AdminRolePermission(role="finance", permission="finance.approve"))
    db_session.commit()
    result = enterprise.workflow_decision(
        enterprise.WorkflowDecisionIn(action="reject"),
        request.id,
        admin=admin,
        db=db_session,
    )
    assert result["status"] == "rejected"


def test_catalog_management_routes_are_registered():
    from backend.api.catalog_management import router

    routes = {(route.path, method) for route in router.routes for method in route.methods or set()}
    assert {
        ("/admin/catalog/archive", "POST"),
        ("/admin/catalog/restore", "POST"),
        ("/admin/catalog/prices", "POST"),
        ("/admin/catalog/category", "POST"),
        ("/admin/catalog/stock", "POST"),
        ("/admin/catalog/archived", "GET"),
    } <= routes

    main_tree = ast.parse((Path(__file__).parents[1] / "main.py").read_text())
    assert any(
        isinstance(node, ast.ImportFrom)
        and node.module == "api.catalog_management"
        and any(alias.asname == "catalog_management_router" for alias in node.names)
        for node in main_tree.body
    )
    assert any(
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "include_router"
        and isinstance(node.value.args[0], ast.Name)
        and node.value.args[0].id == "catalog_management_router"
        for node in main_tree.body
    )


def test_0010_migration_covers_new_model_tables_and_indexes():
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0010_enterprise_telegram_commerce.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0010", migration_path)
    migration = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(migration)

    recorder = SimpleNamespace(tables={}, indexes={})

    def create_table(name, *columns, **kwargs):
        recorder.tables[name] = {column.name: column for column in columns if hasattr(column, "name")}

    def create_index(name, table, columns, **kwargs):
        recorder.indexes[name] = (table, tuple(columns), kwargs)

    migration.op = SimpleNamespace(create_table=create_table, create_index=create_index)
    migration.upgrade()

    model_tables = {
        table.name: table
        for table in [
            *enterprise_models.Base.metadata.tables.values(),
        ]
        if table.name in {
            "product_versions",
            "bulk_edit_jobs",
            "suppliers",
            "supplier_documents",
            "promotion_rules",
            "workflow_definitions",
            "workflow_requests",
            "workflow_actions",
            "media_asset_metadata",
            "telegram_offers",
            "telegram_purchases",
            "gift_certificates",
            "club_memberships",
            "telegram_notification_preferences",
        }
    }
    expected_indexes = {
        index.name: (table.name, tuple(column.name for column in index.columns), index.unique)
        for table in model_tables.values()
        for index in table.indexes
    }

    assert set(recorder.tables) == set(model_tables)
    assert {
        name: (table, columns, bool(kwargs.get("unique")))
        for name, (table, columns, kwargs) in recorder.indexes.items()
    } == expected_indexes
    assert recorder.tables["telegram_purchases"]["telegram_payment_charge_id"].nullable is True
    partial_index = recorder.indexes["ux_telegram_purchases_payment_charge_nonempty"]
    postgresql_where = partial_index[2]["postgresql_where"]
    sqlite_where = partial_index[2]["sqlite_where"]

    normalized_postgresql = normalize_predicate(
        postgresql_where, postgresql.dialect()
    )
    normalized_sqlite = normalize_predicate(sqlite_where, sqlite.dialect())
    expected_predicate = (
        "telegram_payment_charge_id IS NOT NULL "
        "AND telegram_payment_charge_id <> ''"
    )

    assert normalized_postgresql == expected_predicate
    assert normalized_sqlite == expected_predicate
