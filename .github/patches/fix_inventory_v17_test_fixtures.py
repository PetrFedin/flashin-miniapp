from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    Path(path).write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    content = read(path)
    count = content.count(old)
    if count != 1:
        raise SystemExit(
            f"Expected one fixture marker in {path}: {old[:120]!r}; found {count}"
        )
    write(path, content.replace(old, new, 1))


# Use the actual Product, ProductVariant and OrderItem ORM columns.
replace_once(
    "backend/tests/test_inventory_movement_ledger.py",
    '    product = Product(name=f"Ledger product {suffix}", base_price=1000.0)\n',
    '    product = Product(\n'
    '        sku=f"LEDGER-PRODUCT-{suffix}",\n'
    '        title=f"Ledger product {suffix}",\n'
    '        slug=f"ledger-product-{suffix}",\n'
    '        price=1000.0,\n'
    '    )\n',
)
replace_once(
    "backend/tests/test_inventory_movement_ledger.py",
    '        price=1000.0,\n        stock_qty=10,\n',
    '        stock_qty=10,\n',
)

pilot_db_path = "backend/tests/test_pilot_database_evidence.py"
pilot_db = read(pilot_db_path)
old_product = '            product = Product(name=f"Pilot stock {number}", base_price=amount)\n'
new_product = (
    '            product = Product(\n'
    '                sku=f"PILOT-PRODUCT-{number}",\n'
    '                title=f"Pilot stock {number}",\n'
    '                slug=f"pilot-stock-{number}",\n'
    '                price=amount,\n'
    '            )\n'
)
if pilot_db.count(old_product) != 1:
    raise SystemExit("Pilot Product fixture marker missing")
pilot_db = pilot_db.replace(old_product, new_product, 1)
pilot_db = pilot_db.replace(
    '                price=amount,\n                stock_qty=10 - delta,\n',
    '                stock_qty=10 - delta,\n',
    1,
)
old_item = '''                OrderItem(
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
'''
new_item = '''                OrderItem(
                    order_id=number,
                    product_id=product.id,
                    variant_id=variant.id,
                    title=product.title,
                    size=variant.size,
                    quantity=1,
                    price=amount,
                )
'''
if pilot_db.count(old_item) != 1:
    raise SystemExit("Pilot OrderItem fixture marker missing")
pilot_db = pilot_db.replace(old_item, new_item, 1)
write(pilot_db_path, pilot_db)

# Preserve attribution in mocks and assert the exact production metadata.
cancellation_path = "backend/tests/test_order_cancellation_service.py"
cancellation = read(cancellation_path)
cancellation = cancellation.replace(
    '        lambda _db, quantities: released.append(quantities),',
    '        lambda _db, quantities, **kwargs: released.append((quantities, kwargs)),',
    1,
)
cancellation = cancellation.replace(
    '    assert released == [{11: 2, 12: 1}]',
    '    assert released == [\n'
    '        (\n'
    '            {11: 2, 12: 1},\n'
    '            {"order_id": 41, "source": "order_cancellation:provider"},\n'
    '        )\n'
    '    ]',
    1,
)
write(cancellation_path, cancellation)

settlement_path = "backend/tests/test_payment_settlement.py"
settlement = read(settlement_path)
settlement = settlement.replace(
    '        lambda db, quantities: calls.append(("inventory", quantities)),',
    '        lambda db, quantities, **kwargs: calls.append(\n'
    '            ("inventory", quantities, kwargs)\n'
    '        ),',
    1,
)
settlement = settlement.replace(
    '    assert ("inventory", {11: 3, 12: 4}) in calls',
    '    assert (\n'
    '        "inventory",\n'
    '        {11: 3, 12: 4},\n'
    '        {"order_id": 7, "source": "payment_settlement"},\n'
    '    ) in calls',
    1,
)
write(settlement_path, settlement)

replace_once(
    "backend/tests/test_pilot_control_signature.py",
    'assert state["schema_version"] == 6',
    'assert state["schema_version"] == 7',
)

# Repair and expand the synthetic immutable release archive.
capability_path = "backend/tests/test_pilot_release_capability.py"
capability = read(capability_path)
damaged_old_migration = '''    "backend/alembic/versions/0024_inventory_movement_ledger.py": (
        "0024_inventory_movement_ledger\\n0022_pilot_runtime_guard\\n"
        "pilot_state_revision\\npilot_state_sha256\\n"
    ),
'''
restored_old_migration = '''    "backend/alembic/versions/0023_pilot_state_replay_anchor.py": (
        "0023_pilot_state_replay_anchor\\n0022_pilot_runtime_guard\\n"
        "pilot_state_revision\\npilot_state_sha256\\n"
    ),
'''
if capability.count(damaged_old_migration) != 1:
    raise SystemExit("Damaged synthetic replay migration fixture not found")
capability = capability.replace(
    damaged_old_migration,
    restored_old_migration,
    1,
)
capability = capability.replace(
    '"SCHEMA_VERSION = 7\\ndatabase_evidence_contract\\nverified_admission_context(\\n"',
    '"SCHEMA_VERSION = 7\\ndatabase_evidence_contract\\n"\n'
    '        "inventory_evidence_contract\\nverified_admission_context(\\n"',
    1,
)
new_entries = '''    "backend/services/pilot_inventory_evidence.py": (
        "def validate_order_inventory_evidence(): pass\\n"
        "reserve/release\\nreserve/commit\\nsigned stock_before\\n"
        "signed expected_stock_delta\\n"
    ),
    "backend/services/inventory.py": (
        "InventoryMovement(\\nkind=\\\"reserve\\\"\\nkind=\\\"release\\\"\\n"
        "kind=\\\"commit\\\"\\norder_id=order_id\\n"
    ),
    "backend/alembic/versions/0024_inventory_movement_ledger.py": (
        "0024_inventory_movement_ledger\\n0023_pilot_state_replay_anchor\\n"
        "inventory_movements\\nuq_inventory_movement_order_variant_kind\\n"
    ),
    "backend/tests/test_inventory_movement_ledger.py": (
        "test_reserve_and_release_are_one_durable_order_linked_chain\\n"
        "test_reserve_and_commit_capture_stock_and_reserved_snapshots\\n"
        "test_production_inventory_callsites_are_order_attributed\\n"
    ),
'''
anchor = '    "scripts/pilot_control_binding.py": ('
if capability.count(anchor) != 1:
    raise SystemExit("Synthetic capability archive insertion anchor missing")
capability = capability.replace(anchor, new_entries + anchor, 1)
write(capability_path, capability)
