def test_size_helper_returns_size():
    from backend.services.size_helper import suggest_size
    result = suggest_size(height_cm=180, weight_kg=82, usual_size=None, fit_preference="regular")
    assert result["suggested_size"] in {"S", "M", "L", "XL", "XXL"}


def test_product_document_shape():
    from backend.services.meili import product_document

    class Img:
        url = "https://cdn.test/image.jpg"

    class Product:
        id = 1
        sku = "SKU"
        title = "Title"
        brand = "Brand"
        description = "Desc"
        price = 100
        currency = "RUB"
        category = "Cat"
        gender = "unisex"
        active = True
        images = [Img()]

    doc = product_document(Product())
    assert doc["id"] == 1
    assert doc["image_url"].endswith(".jpg")
