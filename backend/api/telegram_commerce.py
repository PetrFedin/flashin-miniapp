from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import Customer
from ..security import get_current_customer
from ..telegram_commerce_models import (
    ClubMembership,
    GiftCertificate,
    TelegramNotificationPreference,
    TelegramOffer,
    TelegramPurchase,
)

router = APIRouter(prefix="/telegram-commerce", tags=["telegram-commerce"])
settings = get_settings()


class OfferOut(BaseModel):
    id: int
    code: str
    title: str
    description: str
    offer_type: str
    stars_amount: int
    duration_days: int
    certificate_value: int


class InvoiceCreateIn(BaseModel):
    offer_code: str = Field(min_length=2, max_length=80)
    recipient_telegram_id: str = Field(default="", max_length=64)
    recipient_username: str = Field(default="", max_length=255)
    gift_message: str = Field(default="", max_length=1000)


class InvoiceOut(BaseModel):
    purchase_id: int
    invoice_url: str
    invoice_payload: str
    stars_amount: int
    status: str


class PaymentConfirmationIn(BaseModel):
    invoice_payload: str
    telegram_payment_charge_id: str
    provider_payment_charge_id: str = ""
    total_amount: int
    currency: Literal["XTR"] = "XTR"


class NotificationPreferenceIn(BaseModel):
    event_type: str = Field(min_length=2, max_length=64)
    enabled: bool = True
    quiet_hours_start: str = Field(default="", max_length=5)
    quiet_hours_end: str = Field(default="", max_length=5)


class RefundIn(BaseModel):
    telegram_user_id: int
    telegram_payment_charge_id: str


def _active_offer(db: Session, code: str) -> TelegramOffer:
    now = datetime.utcnow()
    offer = db.query(TelegramOffer).filter(TelegramOffer.code == code, TelegramOffer.active.is_(True)).first()
    if not offer:
        raise HTTPException(status_code=404, detail="Telegram offer not found")
    if offer.starts_at and offer.starts_at > now:
        raise HTTPException(status_code=409, detail="Offer has not started")
    if offer.ends_at and offer.ends_at < now:
        raise HTTPException(status_code=409, detail="Offer has ended")
    return offer


def _telegram_api(method: str, payload: dict) -> dict:
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"
    try:
        response = httpx.post(url, json=payload, timeout=15.0)
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Telegram API unavailable: {exc}") from exc
    if not body.get("ok"):
        raise HTTPException(status_code=502, detail=body.get("description", "Telegram API error"))
    return body.get("result")


def _fulfill_purchase(db: Session, purchase: TelegramPurchase) -> None:
    offer = purchase.offer
    now = datetime.utcnow()

    if offer.offer_type == "club":
        membership = db.query(ClubMembership).filter(ClubMembership.customer_id == purchase.customer_id).first()
        starts_at = now
        if membership and membership.status == "active" and membership.expires_at > now:
            starts_at = membership.expires_at
        expires_at = starts_at + timedelta(days=max(offer.duration_days, 1))
        if membership:
            membership.status = "active"
            membership.starts_at = min(membership.starts_at, now)
            membership.expires_at = expires_at
            membership.source_purchase_id = purchase.id
            membership.updated_at = now
        else:
            db.add(
                ClubMembership(
                    customer_id=purchase.customer_id,
                    status="active",
                    starts_at=now,
                    expires_at=expires_at,
                    source_purchase_id=purchase.id,
                )
            )

    if offer.offer_type == "certificate":
        exists = db.query(GiftCertificate).filter(GiftCertificate.purchase_id == purchase.id).first()
        if not exists:
            code = f"FL-{secrets.token_hex(6).upper()}"
            db.add(
                GiftCertificate(
                    purchase_id=purchase.id,
                    code=code,
                    value_rub=offer.certificate_value,
                    balance_rub=offer.certificate_value,
                    owner_customer_id=None if purchase.recipient_telegram_id else purchase.customer_id,
                    recipient_telegram_id=purchase.recipient_telegram_id,
                    recipient_username=purchase.recipient_username,
                    expires_at=now + timedelta(days=365),
                )
            )


@router.get("/offers", response_model=list[OfferOut])
def list_offers(db: Session = Depends(get_db)):
    now = datetime.utcnow()
    offers = db.query(TelegramOffer).filter(TelegramOffer.active.is_(True)).order_by(TelegramOffer.id.asc()).all()
    return [
        offer
        for offer in offers
        if (not offer.starts_at or offer.starts_at <= now) and (not offer.ends_at or offer.ends_at >= now)
    ]


@router.post("/invoice", response_model=InvoiceOut, status_code=status.HTTP_201_CREATED)
def create_invoice(
    payload: InvoiceCreateIn,
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    offer = _active_offer(db, payload.offer_code)
    if offer.offer_type not in {"club", "certificate", "drop"}:
        raise HTTPException(status_code=422, detail="Unsupported Telegram offer type")

    invoice_payload = f"flashin:{customer.id}:{offer.id}:{secrets.token_urlsafe(18)}"
    invoice_url = _telegram_api(
        "createInvoiceLink",
        {
            "title": offer.title[:32],
            "description": (offer.description or offer.title)[:255],
            "payload": invoice_payload,
            "provider_token": "",
            "currency": "XTR",
            "prices": [{"label": offer.title[:32], "amount": offer.stars_amount}],
        },
    )

    purchase = TelegramPurchase(
        customer_id=customer.id,
        offer_id=offer.id,
        invoice_payload=invoice_payload,
        invoice_url=invoice_url,
        stars_amount=offer.stars_amount,
        recipient_telegram_id=payload.recipient_telegram_id.strip(),
        recipient_username=payload.recipient_username.strip().lstrip("@"),
        gift_message=payload.gift_message.strip(),
    )
    db.add(purchase)
    db.commit()
    db.refresh(purchase)
    return InvoiceOut(
        purchase_id=purchase.id,
        invoice_url=purchase.invoice_url,
        invoice_payload=purchase.invoice_payload,
        stars_amount=purchase.stars_amount,
        status=purchase.status,
    )


@router.post("/payments/confirm")
def confirm_payment(
    payload: PaymentConfirmationIn,
    x_telegram_webhook_secret: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    if not settings.telegram_webhook_secret or not secrets.compare_digest(
        x_telegram_webhook_secret or "", settings.telegram_webhook_secret
    ):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    purchase = db.query(TelegramPurchase).filter(TelegramPurchase.invoice_payload == payload.invoice_payload).first()
    if not purchase:
        raise HTTPException(status_code=404, detail="Purchase not found")
    if purchase.status == "paid":
        return {"status": "paid", "purchase_id": purchase.id, "idempotent": True}
    if payload.total_amount != purchase.stars_amount:
        raise HTTPException(status_code=409, detail="Stars amount mismatch")

    purchase.status = "paid"
    purchase.telegram_payment_charge_id = payload.telegram_payment_charge_id
    purchase.provider_payment_charge_id = payload.provider_payment_charge_id
    purchase.paid_at = datetime.utcnow()
    _fulfill_purchase(db, purchase)
    db.commit()
    return {"status": "paid", "purchase_id": purchase.id, "idempotent": False}


@router.get("/purchases")
def list_purchases(
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(TelegramPurchase)
        .filter(TelegramPurchase.customer_id == customer.id)
        .order_by(TelegramPurchase.created_at.desc())
        .limit(100)
        .all()
    )
    return [
        {
            "id": row.id,
            "offer_code": row.offer.code,
            "offer_title": row.offer.title,
            "offer_type": row.offer.offer_type,
            "stars_amount": row.stars_amount,
            "status": row.status,
            "recipient_telegram_id": row.recipient_telegram_id,
            "recipient_username": row.recipient_username,
            "paid_at": row.paid_at,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.get("/membership")
def membership(
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    row = db.query(ClubMembership).filter(ClubMembership.customer_id == customer.id).first()
    if not row:
        return {"active": False}
    active = row.status == "active" and row.expires_at > datetime.utcnow()
    return {
        "active": active,
        "level": row.level,
        "starts_at": row.starts_at,
        "expires_at": row.expires_at,
        "auto_renew": row.auto_renew,
    }


@router.get("/certificates")
def certificates(
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(GiftCertificate)
        .filter(GiftCertificate.owner_customer_id == customer.id)
        .order_by(GiftCertificate.created_at.desc())
        .all()
    )
    return rows


@router.put("/notification-preferences")
def set_notification_preference(
    payload: NotificationPreferenceIn,
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    allowed = {"order_status", "restock", "price_drop", "personal_offer", "abandoned_cart", "club_expiry"}
    if payload.event_type not in allowed:
        raise HTTPException(status_code=422, detail="Unsupported notification event type")
    row = (
        db.query(TelegramNotificationPreference)
        .filter(
            TelegramNotificationPreference.customer_id == customer.id,
            TelegramNotificationPreference.event_type == payload.event_type,
        )
        .first()
    )
    if not row:
        row = TelegramNotificationPreference(customer_id=customer.id, event_type=payload.event_type)
        db.add(row)
    row.enabled = payload.enabled
    row.quiet_hours_start = payload.quiet_hours_start
    row.quiet_hours_end = payload.quiet_hours_end
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return row


@router.post("/payments/refund")
def refund_stars(
    payload: RefundIn,
    x_telegram_webhook_secret: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    if not settings.telegram_webhook_secret or not secrets.compare_digest(
        x_telegram_webhook_secret or "", settings.telegram_webhook_secret
    ):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")
    purchase = (
        db.query(TelegramPurchase)
        .filter(TelegramPurchase.telegram_payment_charge_id == payload.telegram_payment_charge_id)
        .first()
    )
    if not purchase:
        raise HTTPException(status_code=404, detail="Purchase not found")
    if purchase.status == "refunded":
        return {"status": "refunded", "idempotent": True}

    _telegram_api(
        "refundStarPayment",
        {
            "user_id": payload.telegram_user_id,
            "telegram_payment_charge_id": payload.telegram_payment_charge_id,
        },
    )
    purchase.status = "refunded"
    membership = db.query(ClubMembership).filter(ClubMembership.source_purchase_id == purchase.id).first()
    if membership:
        membership.status = "revoked"
        membership.updated_at = datetime.utcnow()
    certificate = db.query(GiftCertificate).filter(GiftCertificate.purchase_id == purchase.id).first()
    if certificate:
        certificate.status = "revoked"
        certificate.balance_rub = 0
    db.commit()
    return {"status": "refunded", "idempotent": False}
