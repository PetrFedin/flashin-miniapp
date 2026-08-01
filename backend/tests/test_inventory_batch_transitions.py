from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.services.inventory import commit_reservations_to_sold, release_variants


class FakeQuery:
    def __init__(self, variants):
        self.variants = variants

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def with_for_update(self):
        return self

    def all(self):
        return sorted(self.variants, key=lambda variant: variant.id)


class FakeSession:
    def __init__(self, variants):
        self.variants = variants

    def query(self, _model):
        return FakeQuery(self.variants)


def variant(variant_id, *, stock, reserved):
    return SimpleNamespace(
        id=variant_id,
        stock_qty=stock,
        reserved_qty=reserved,
        size=str(variant_id),
    )


def test_release_validates_all_variants_before_mutating_any():
    first = variant(1, stock=10, reserved=5)
    second = variant(2, stock=10, reserved=1)

    with pytest.raises(HTTPException, match="variant 2") as exc_info:
        release_variants(FakeSession([first, second]), {1: 3, 2: 2})

    assert exc_info.value.status_code == 409
    assert first.reserved_qty == 5
    assert second.reserved_qty == 1


def test_commit_to_sold_validates_all_variants_before_mutating_any():
    first = variant(1, stock=10, reserved=5)
    second = variant(2, stock=1, reserved=2)

    with pytest.raises(HTTPException, match="Reserved quantity exceeds stock"):
        commit_reservations_to_sold(FakeSession([first, second]), {1: 2, 2: 2})

    assert first.stock_qty == 10
    assert first.reserved_qty == 5
    assert second.stock_qty == 1
    assert second.reserved_qty == 2


def test_release_aggregates_and_applies_full_batch():
    first = variant(1, stock=10, reserved=5)
    second = variant(2, stock=8, reserved=4)

    release_variants(FakeSession([first, second]), {1: 3, 2: 4})

    assert first.reserved_qty == 2
    assert second.reserved_qty == 0


def test_commit_to_sold_applies_stock_and_reservation_together():
    first = variant(1, stock=10, reserved=5)
    second = variant(2, stock=8, reserved=4)

    commit_reservations_to_sold(FakeSession([first, second]), {1: 3, 2: 2})

    assert (first.stock_qty, first.reserved_qty) == (7, 2)
    assert (second.stock_qty, second.reserved_qty) == (6, 2)
