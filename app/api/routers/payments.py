from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.payment import (
    confirm_payment_order,
    create_pix_checkout,
    get_order_by_id,
    PRO_PRICE_CENTS,
)
from app.db.models import PaymentOrder, User

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["payments"])


class CheckoutPixRequest(BaseModel):
    plan_tier: str = "pro"
    gateway: str = "mock"  # mock | asaas | mercadopago


class CheckoutPixResponse(BaseModel):
    order_id: str
    status: str
    amount_cents: int
    amount_reais: float
    pix_copy_paste: str
    pix_qr_code: str
    expires_at: str


@router.post("/checkout/pix", response_model=CheckoutPixResponse)
def checkout_pix(
    data: CheckoutPixRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CheckoutPixResponse:
    """Generate a PIX checkout order for the current user."""
    order = create_pix_checkout(
        db=db,
        user=user,
        plan_tier=data.plan_tier,
        amount_cents=PRO_PRICE_CENTS,
        gateway=data.gateway,
    )
    return CheckoutPixResponse(
        order_id=order.order_id,
        status=order.status,
        amount_cents=order.amount_cents,
        amount_reais=order.amount_cents / 100.0,
        pix_copy_paste=order.pix_copy_paste or "",
        pix_qr_code=order.pix_qr_code or "",
        expires_at=order.expires_at or "",
    )


@router.get("/checkout/order/{order_id}/status")
def check_order_status(
    order_id: str,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Check the status of an ongoing checkout order."""
    order = get_order_by_id(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Pedido não encontrado.")

    return {
        "order_id": order.order_id,
        "status": order.status,
        "plan_tier": order.plan_tier,
        "paid_at": order.paid_at,
    }


@router.post("/checkout/order/{order_id}/simulate-pay")
def simulate_order_pay(
    order_id: str,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Development/Sandbox helper: instantly simulate a successful PIX payment."""
    order = get_order_by_id(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Pedido não encontrado.")

    confirmed = confirm_payment_order(db, order, gateway_payment_id="sim_pix_123")
    return {
        "ok": True,
        "order_id": confirmed.order_id,
        "status": confirmed.status,
        "user_plan": confirmed.user.plan_tier if confirmed.user else None,
        "message": "Pagamento PIX simulado com sucesso! Plano Pro ativado.",
    }


@router.post("/payments/webhook")
async def payment_webhook(
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Universal Webhook handler for Asaas and Mercado Pago."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    log.info("payment.webhook_received", body=body)

    # 1. Asaas format: {"event": "PAYMENT_RECEIVED", "payment": {"id": "...", "externalReference": "ord_..."}}
    event = body.get("event")
    if event in ("PAYMENT_RECEIVED", "PAYMENT_CONFIRMED"):
        payment_data = body.get("payment", {})
        ref = payment_data.get("externalReference")
        if ref:
            order = get_order_by_id(db, ref)
            if order:
                confirm_payment_order(db, order, gateway_payment_id=payment_data.get("id"))
                return {"ok": True, "processed": "asaas"}

    # 2. Mercado Pago format: {"action": "payment.updated", "data": {"id": "..."}}
    # In MP, we query the payment or external_reference if sent in webhook
    order_id_hint = request.query_params.get("order_id") or body.get("external_reference")
    if order_id_hint:
        order = get_order_by_id(db, order_id_hint)
        if order:
            confirm_payment_order(db, order, gateway_payment_id=str(body.get("data", {}).get("id")))
            return {"ok": True, "processed": "mercadopago"}

    return {"ok": True, "processed": "unmatched"}
