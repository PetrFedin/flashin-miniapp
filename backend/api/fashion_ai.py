from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
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
   