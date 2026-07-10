def test_moysklad_stock_parser():
    from backend.services.moysklad import _stock_from_moysklad
    assert _stock_from_moysklad({"quantity": 3}) == 3
    assert _stock_from_moysklad({"stock": 2}) == 2
    assert _stock_from_moysklad({"effectiveStock": 1}) == 1
