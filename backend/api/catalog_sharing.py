from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import Product
from ..services.telegram_product_links import product_share_links

router = APIRouter(prefix="/catalog", tags=["catalog-sharing"])
settings = get_settings()


@router.get("/products/{product_id}/share")
def product_share(product_id: int, db: Session = Depends(get_db)):
    product = (
        db.query(Product)
        .filter(Product.id == product_id, Product.active.is_(True))
        .first()
    )
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product_share_links(
        product.id,
        product.title,
        mini_app_url=settings.mini_app_url,
    )
