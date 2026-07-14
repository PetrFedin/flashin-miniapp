from datetime import datetime
from pydantic import BaseModel, Field


class ImageOut(BaseModel):
    id: int
    url: str
    storage_key: str = ""
    sort_order: int
    model_config = {"from_attributes": True}


class VariantOut(BaseModel):
    id: int
    size: str
    color: str = ""
    sku: str
    stock_qty: int
    reserved_qty: int
    available_qty: int
    model_config = {"from_attributes": True