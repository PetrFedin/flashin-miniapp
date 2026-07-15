from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..models import Product
from ..services.fashion_ai import calculate_style_match_score, generate_style_prompts

router = APIRouter(prefix="/fashion-ai", tags=["fashion-ai"])


def _product_tags(product: Product) -> list[str]:
   