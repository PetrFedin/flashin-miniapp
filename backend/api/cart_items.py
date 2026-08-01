"""Compatibility exports for the consolidated cart item mutation API.

The public PATCH route now lives in ``backend.api.cart`` so quantity changes use
the same transactional adjustment reconciliation as every other cart mutation.
This module intentionally exposes an empty router because older imports may still
reference ``cart_items.router`` or ``update_cart_item_quantity``.
"""

from fastapi import APIRouter

from .cart import update_item as update_cart_item_quantity


router = APIRouter(prefix="/cart", tags=["cart"])

__all__ = ["router", "update_cart_item_quantity"]
