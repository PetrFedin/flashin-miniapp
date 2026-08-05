from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    Path(path).write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Expected one match in {path}: {old[:100]!r}; found {count}")
    write(path, text.replace(old, new, 1))


# ORM model.
replace_once(
    "backend/models.py",
    "from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint",
    "from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint",
)
model = '''class InventoryMovement(Base):
    __tablename__ = "inventory_movements"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('reserve', 'release', 'commit')",
            name="ck_inventory_movements_kind",
        ),
        CheckConstraint(
            "quantity > 0",
            name="ck_inventory_movements_quantity_positive",
        ),
        UniqueConstraint(
            "order_id",
            "variant_id",
            "kind",
            name="uq_inventory_movement_order_variant_kind",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    variant_id: Mapped[int] = mapped_column(
        ForeignKey("product_variants.id", ondelete="RESTRICT"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    quantity: Mapped[int] = mapped_column(Integer)
    stock_before: Mapped[int] = mapped_column(Integer)
    stock_after: Mapped[int] = mapped_column(Integer)
    reserved_before: Mapped[int] = mapped_column(Integer)
    reserved_after: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


'''
models = read("backend/models.py")
anchor = "class InventoryAdjustment(Base):"
if models.count(anchor) != 1:
    raise SystemExit("InventoryAdjustment model anchor missing")
write("backend/models.py", models.replace(anchor, model + anchor, 1))

# Production inventory callsites.
replace_once(
    "backend/api/orders.py",
    "variant = reserve_variant(db, cart_item.variant_id, cart_item.quantity)",
    '''variant = reserve_variant(
                db,
                cart_item.variant_id,
                cart_item.quantity,
                order_id=order.id,
                source="checkout",
            )''',
)
replace_once(
    "backend/services/order_cancellation.py",
    "    release_variants(db, quantities)\n",
    '''    release_variants(
        db,
        quantities,
        order_id=order.id,
        source=f"order_cancellation:{source}",
    )
''',
)
replace_once(
    "backend/services/payment_settlement.py",
    "    commit_reservations_to_sold(db, _item_quantities(order))\n",
    '''    commit_reservations_to_sold(
        db,
        _item_quantities(order),
        order_id=order.id,
        source="payment_settlement",
    )
''',
)

# Pilot DB verifier invokes movement verifier and schema v7 contract.
replace_once(
    "backend/services/pilot_database_evidence.py",
    "from ..models import Order, Payment, ReturnRequest\n",
    "from ..models import Order, Payment, ReturnRequest\n"
    "from .pilot_inventory_evidence import validate_order_inventory_evidence\n",
)
replace_once(
    "backend/services/pilot_database_evidence.py",
    '    if pilot_state.get("schema_version") != 6:',
    '    if pilot_state.get("schema_version") != 7:',
)
replace_once(
    "backend/services/pilot_database_evidence.py",
    '''    if (
        pilot_state.get("database_evidence_contract")
        != DATABASE_EVIDENCE_CONTRACT
    ):
        return ["pilot database evidence contract is missing or unsupported"]
''',
    '''    if (
        pilot_state.get("database_evidence_contract")
        != DATABASE_EVIDENCE_CONTRACT
    ):
        return ["pilot database evidence contract is missing or unsupported"]
    if pilot_state.get("inventory_evidence_contract") != 1:
        return ["pilot inventory evidence contract is missing or unsupported"]
''',
)
replace_once(
    "backend/services/pilot_database_evidence.py",
    '''        if slot is not None and slot.customer_id != order.customer_id:
            errors.append(
                f"#{number}: pilot slot customer does not own PostgreSQL order {order_id}"
            )

        recorded_order_status''',
    '''        if slot is not None and slot.customer_id != order.customer_id:
            errors.append(
                f"#{number}: pilot slot customer does not own PostgreSQL order {order_id}"
            )
        errors.extend(validate_order_inventory_evidence(db, record, order))

        recorded_order_status''',
)

# Signed state schema v7.
replace_once("scripts/pilot_control.py", "SCHEMA_VERSION = 6", "SCHEMA_VERSION = 7")
replace_once(
    "scripts/pilot_control.py",
    '        "database_evidence_contract": 1,\n        "pilot_name":',
    '        "database_evidence_contract": 1,\n'
    '        "inventory_evidence_contract": 1,\n'
    '        "pilot_name":',
)
replace_once(
    "scripts/pilot_control.py",
    '''    if schema == 5:
        raise ValueError(
            "Database-unverified pilot state schema 5 cannot be reused. Archive it and "
            "initialize a fresh database-bound pilot state."
        )
    if schema != SCHEMA_VERSION:
''',
    '''    if schema == 5:
        raise ValueError(
            "Database-unverified pilot state schema 5 cannot be reused. Archive it and "
            "initialize a fresh database-bound pilot state."
        )
    if schema == 6:
        raise ValueError(
            "Inventory-unverified pilot state schema 6 cannot be reused. Archive it and "
            "initialize a fresh inventory-ledger-bound pilot state."
        )
    if schema != SCHEMA_VERSION:
''',
)
replace_once(
    "scripts/pilot_control.py",
    '    if state.get("database_evidence_contract") != 1:\n'
    '        raise ValueError("Pilot database evidence contract is missing or unsupported")\n',
    '    if state.get("database_evidence_contract") != 1:\n'
    '        raise ValueError("Pilot database evidence contract is missing or unsupported")\n'
    '    if state.get("inventory_evidence_contract") != 1:\n'
    '        raise ValueError("Pilot inventory evidence contract is missing or unsupported")\n',
)

# Runtime schema checks.
for path in ("backend/services/pilot_runtime.py", "scripts/pilot_runtime.py"):
    text = read(path)
    text = text.replace('pilot_state.get("schema_version") != 6', 'pilot_state.get("schema_version") != 7')
    marker = 'pilot_state.get("database_evidence_contract") != 1'
    if marker not in text:
        raise SystemExit(f"DB contract marker missing in {path}")
    if "inventory_evidence_contract" not in text:
        if path.startswith("backend/"):
            old = '''    elif pilot_state.get("database_evidence_contract") != 1:
        errors.append("pilot database evidence contract is missing or unsupported")
'''
            new = old + '''    elif pilot_state.get("inventory_evidence_contract") != 1:
        errors.append("pilot inventory evidence contract is missing or unsupported")
'''
        else:
            old = '''        if pilot_state.get("database_evidence_contract") != 1:
            raise ValueError("Pilot database evidence contract is missing or unsupported")
'''
            new = old + '''        if pilot_state.get("inventory_evidence_contract") != 1:
            raise ValueError("Pilot inventory evidence contract is missing or unsupported")
'''
        if old not in text:
            raise SystemExit(f"Runtime contract block missing in {path}")
        text = text.replace(old, new, 1)
    write(path, text)

# Capability v17 and required artifacts.
replace_once(
    "scripts/pilot_release_contract.py",
    "CAPABILITY_VERSION = 16",
    "CAPABILITY_VERSION = 17",
)
capability = read("scripts/pilot_release_capability.py")
capability = capability.replace(
    '    "backend/services/pilot_database_evidence.py",\n',
    '    "backend/services/pilot_database_evidence.py",\n'
    '    "backend/services/pilot_inventory_evidence.py",\n'
    '    "backend/services/inventory.py",\n'
    '    "backend/alembic/versions/0024_inventory_movement_ledger.py",\n',
    1,
)
capability = capability.replace(
    '    "backend/tests/test_pilot_database_evidence.py",\n',
    '    "backend/tests/test_pilot_database_evidence.py",\n'
    '    "backend/tests/test_inventory_movement_ledger.py",\n',
    1,
)
capability = capability.replace(
    '("CAPABILITY_VERSION = 16",)',
    '("CAPABILITY_VERSION = 17",)',
    1,
)
capability = capability.replace(
    '("SCHEMA_VERSION = 6", "database_evidence_contract",',
    '("SCHEMA_VERSION = 7", "database_evidence_contract", "inventory_evidence_contract",',
    1,
)
marker = '            _require_markers(bundle, files, "backend/services/pilot_database_evidence.py",'
pos = capability.find(marker)
if pos < 0:
    raise SystemExit("Capability DB evidence marker missing")
line_end = capability.find("\n", pos)
capability = (
    capability[: line_end + 1]
    + '            _require_markers(bundle, files, "backend/services/pilot_inventory_evidence.py", '
      '("def validate_order_inventory_evidence(", "reserve/release", "reserve/commit", '
      '"signed stock_before", "signed expected_stock_delta"), errors)\n'
    + '            _require_markers(bundle, files, "backend/services/inventory.py", '
      '("InventoryMovement(", "kind=\"reserve\"", "kind=\"release\"", '
      '"kind=\"commit\"", "order_id=order_id"), errors)\n'
    + '            _require_markers(bundle, files, "backend/alembic/versions/0024_inventory_movement_ledger.py", '
      '("revision = \"0024_inventory_movement_ledger\"", '
      '"down_revision = \"0023_pilot_state_replay_anchor\"", '
      '"inventory_movements", "uq_inventory_movement_order_variant_kind"), errors)\n'
    + '            _require_markers(bundle, files, "backend/tests/test_inventory_movement_ledger.py", '
      '("test_reserve_and_release_are_one_durable_order_linked_chain", '
      '"test_reserve_and_commit_capture_stock_and_reserved_snapshots", '
      '"test_production_inventory_callsites_are_order_attributed"), errors)\n'
    + capability[line_end + 1 :]
)
write("scripts/pilot_release_capability.py", capability)

# Current pilot fixtures move to schema v7 and include the inventory contract.
for path in Path("backend/tests").glob("test_pilot*.py"):
    text = path.read_text(encoding="utf-8")
    text = text.replace('"schema_version": 6', '"schema_version": 7')
    text = text.replace("schema_version == 6", "schema_version == 7")
    text = text.replace("SCHEMA_VERSION == 6", "SCHEMA_VERSION == 7")
    text = text.replace('schema_version") != 6', 'schema_version") != 7')
    text = text.replace('"version": 16', '"version": 17')
    text = text.replace("CAPABILITY_VERSION = 16", "CAPABILITY_VERSION = 17")
    text = text.replace("CAPABILITY_VERSION == 16", "CAPABILITY_VERSION == 17")
    text = text.replace("capability_v16", "capability_v17")
    text = text.replace("capability-v16", "capability-v17")
    if '"database_evidence_contract": 1,' in text and '"inventory_evidence_contract": 1,' not in text:
        text = text.replace(
            '"database_evidence_contract": 1,',
            '"database_evidence_contract": 1,\n        "inventory_evidence_contract": 1,',
        )
    path.write_text(text, encoding="utf-8")

# Expand DB evidence fixture with order items and movement chains.
path = "backend/tests/test_pilot_database_evidence.py"
text = read(path)
text = text.replace(
    "from backend.models import Customer, Order, Payment, ReturnRequest",
    "from backend.models import (\n"
    "    Customer,\n    InventoryMovement,\n    Order,\n    OrderItem,\n"
    "    Payment,\n    Product,\n    ProductVariant,\n    ReturnRequest,\n)",
    1,
)
old = '''        session.add(order)
        session.add(
            PilotOrderSlot(
'''
new = '''        session.add(order)
        session.flush()
        if scenario.get("requires_stock"):
            delta = int(scenario.get("expected_stock_delta", 1))
            product = Product(name=f"Pilot stock {number}", base_price=amount)
            session.add(product)
            session.flush()
            variant = ProductVariant(
                product_id=product.id,
                sku=f"PILOT-STOCK-{number}",
                size="ONE",
                price=amount,
                stock_qty=10 - delta,
                reserved_qty=0,
            )
            session.add(variant)
            session.flush()
            session.add(
                OrderItem(
                    order_id=number,
                    product_id=product.id,
                    variant_id=variant.id,
                    name=product.name,
                    sku=variant.sku,
                    size=variant.size,
                    quantity=1,
                    unit_price=amount,
                    total_price=amount,
                )
            )
            session.add(
                InventoryMovement(
                    order_id=number,
                    variant_id=variant.id,
                    kind="reserve",
                    quantity=1,
                    stock_before=10,
                    stock_after=10,
                    reserved_before=0,
                    reserved_after=1,
                    source="checkout",
                )
            )
            session.add(
                InventoryMovement(
                    order_id=number,
                    variant_id=variant.id,
                    kind="release" if delta == 0 else "commit",
                    quantity=1,
                    stock_before=10,
                    stock_after=10 - delta,
                    reserved_before=1,
                    reserved_after=0,
                    source=(
                        "order_cancellation:pilot"
                        if delta == 0
                        else "payment_settlement"
                    ),
                )
            )
        session.add(
            PilotOrderSlot(
'''
if old not in text:
    raise SystemExit("Pilot DB fixture order anchor missing")
text = text.replace(old, new, 1)
text += '''


def test_stock_claim_is_read_from_contiguous_inventory_movements():
    session, runtime = populated_session()
    state = state_with_passed_scenarios()
    stock_record = next(
        record for record in state["scenarios"] if record.get("stock_before") is not None
    )
    stock_record["stock_after"] = 999
    try:
        errors = validate_pilot_database_evidence(session, state, runtime)
        assert any("signed stock_after" in error for error in errors)
    finally:
        session.close()


def test_missing_or_broken_inventory_chain_fails_closed():
    session, runtime = populated_session()
    state = state_with_passed_scenarios()
    stock_record = next(
        record for record in state["scenarios"] if record.get("stock_before") is not None
    )
    order_id = int(stock_record["order_id"])
    movement = (
        session.query(InventoryMovement)
        .filter(
            InventoryMovement.order_id == order_id,
            InventoryMovement.kind == "reserve",
        )
        .one()
    )
    movement.reserved_after = 9
    session.commit()
    try:
        errors = validate_pilot_database_evidence(session, state, runtime)
        assert any("reserve inventory transition is invalid" in error for error in errors)
    finally:
        session.close()
'''
write(path, text)

# Migration head assertions.
for path in Path("backend/tests").glob("test_*.py"):
    text = path.read_text(encoding="utf-8")
    if "0023_pilot_state_replay_anchor" in text:
        path.write_text(
            text.replace(
                "0023_pilot_state_replay_anchor",
                "0024_inventory_movement_ledger",
            ),
            encoding="utf-8",
        )

# Docs.
runbook = read("docs/pilot/admission_bound_state_migration.md")
runbook = runbook.replace("state schema v6", "state schema v7")
runbook = runbook.replace("capability v16", "capability v17")
runbook = runbook.replace(
    "Schema v5 has accountable signed mutations but does not prove recorded IDs against PostgreSQL. All five",
    "Schema v5 has accountable signed mutations but does not prove recorded IDs against PostgreSQL. Schema v6 verifies order/payment/refund rows but not durable inventory movements. All six",
)
runbook = runbook.replace(
    "Existing schema v1, v2, v3, v4 or v5 state",
    "Existing schema v1, v2, v3, v4, v5 or v6 state",
)
runbook = runbook.replace(
    "database-bound schema v6 state",
    "inventory-ledger-bound schema v7 state",
)
runbook += '''

## Inventory movement evidence

Every production reserve, release and commit writes an order-linked
`inventory_movements` row in the same transaction as the stock mutation.
Each order-item variant has one reserve and at most one terminal release or
commit, with contiguous stock/reserved before-and-after values. Pilot stock
claims are calculated from this ledger; manually typed stock numbers cannot
produce GO. Missing, duplicated, non-contiguous or status-incompatible
movement chains fail closed.
'''
write("docs/pilot/admission_bound_state_migration.md", runbook)
matrix = read("docs/pilot/end_to_end_coverage_matrix.md")
row = "| Durable inventory evidence | Checkout reserve → order-linked movement ledger → payment commit/cancellation release → signed pilot stock verification | PASS | Capability v17 validates quantity, sequence, before/after continuity and order-status-compatible terminal movement. |"
if row not in matrix:
    matrix += "\n" + row + "\n"
write("docs/pilot/end_to_end_coverage_matrix.md", matrix)
