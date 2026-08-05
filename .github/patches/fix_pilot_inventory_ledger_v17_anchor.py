from pathlib import Path

path = Path(".github/patches/apply_pilot_inventory_ledger_v17.py")
content = path.read_text(encoding="utf-8")
old = '''replace_once(
    "backend/services/payment_settlement.py",
    "    commit_reservations_to_sold(db, _item_quantities(order))\\n",
    ''' + "'''" + '''    commit_reservations_to_sold(
        db,
        _item_quantities(order),
        order_id=order.id,
        source="payment_settlement",
    )
''' + "'''" + ''',
)
'''
new = '''replace_once(
    "backend/services/payment_settlement.py",
    "    commit_reservations_to_sold(db, _order_item_quantities(order))\\n",
    ''' + "'''" + '''    commit_reservations_to_sold(
        db,
        _order_item_quantities(order),
        order_id=order.id,
        source="payment_settlement",
    )
''' + "'''" + ''',
)
'''
if content.count(old) != 1:
    raise SystemExit(
        f"Expected one obsolete payment-settlement patch block; found {content.count(old)}"
    )
path.write_text(content.replace(old, new, 1), encoding="utf-8")
