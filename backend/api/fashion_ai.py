from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..models import Product
from ..services.fashion_ai import (
    build_product_style_context,
    calculate_style_match_score,
    generate_style_prompts,
)

router = APIRouter(prefix="/fashion-ai", tags=["fashion-ai"])


class StyleMatchIn(BaseModel):
    preferences: list[str] = Field(default_factory=list, max_length=50)


class StyleMatchOut(BaseModel):
    product_id: int
    score: int


class ProductStyleOut(BaseModel):
    product_id: int
    title: str
    image_url: str | None
    context: dict[str, Any]
    prompts: list[str]


def _get_product(product_id: int, db: Session) -> Product:
    product = (
        db.query(Product)
        .options(joinedload(Product.images), joinedload(Product.variants))
        .filter(Product.id == product_id, Product.active.is_(True))
        .first()
    )
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


def _product_data(product: Product) -> dict[str, Any]:
    colors = sorted({variant.color for variant in product.variants if variant.color})
    return {
        "brand": product.brand,
        "category": product.category,
        "gender": product.gender,
        "color": colors[0] if colors else "",
        "price": product.price,
        "tags": [product.brand, product.category, product.gender, *colors],
    }


@router.get("/products/{product_id}", response_model=ProductStyleOut)
def product_style(product_id: int, db: Session = Depends(get_db)) -> ProductStyleOut:
    product = _get_product(product_id, db)
    product_data = _product_data(product)
    first_image = min(product.images, key=lambda image: image.sort_order, default=None)
    return ProductStyleOut(
        product_id=product.id,
        title=product.title,
        image_url=first_image.url if first_image else None,
        context=build_product_style_context(product_data),
        prompts=generate_style_prompts(product_data),
    )


@router.post("/products/{product_id}/match", response_model=StyleMatchOut)
def style_match(
    product_id: int,
    payload: StyleMatchIn,
    db: Session = Depends(get_db),
) -> StyleMatchOut:
    product = _get_product(product_id, db)
    score = calculate_style_match_score(
        _product_data(product)["tags"],
        payload.preferences,
    )
    return StyleMatchOut(product_id=product.id, score=score)
