from __future__ import annotations

from datetime import datetime, timezone, timedelta
import secrets
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.core.security import now_iso
from app.db.models import PaymentOrder, User

log = structlog.get_logger(__name__)

PRO_PRICE_CENTS = 3990  # R$ 39,90/mês


def create_pix_checkout(
    db: Session,
    user: User,
    plan_tier: str = "pro",
    amount_cents: int = PRO_PRICE_CENTS,
    gateway: str = "mock",
) -> PaymentOrder:
    """Create a new pending PIX order for subscription."""
    order_id = f"ord_{secrets.token_hex(12)}"
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Generate a realistic mock PIX payload if in mock mode
    if gateway == "mock":
        pix_copy_paste = (
            f"00020126580014br.gov.bcb.pix0136{order_id}"
            f"5204000053039865405{amount_cents/100:.2f}5802BR5913FC27 CLUBS6009SAO PAULO"
            f"62070503***6304{secrets.token_hex(2).upper()}"
        )
        # Simple SVG/Data placeholder for QR Code
        pix_qr_code = f"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='200' height='200'><rect width='200' height='200' fill='%23111827'/><text x='50%25' y='50%25' dominant-baseline='middle' text-anchor='middle' fill='%2322c55e' font-family='sans-serif' font-size='14'>PIX R$ {amount_cents/100:.2f}</text></svg>"
    else:
        # In real gateway mode (Asaas / Mercado Pago), the external API would be called.
        pix_copy_paste = f"00020126580014br.gov.bcb.pix0136{order_id}"
        pix_qr_code = ""

    order = PaymentOrder(
        order_id=order_id,
        user_id=user.id,
        amount_cents=amount_cents,
        currency="BRL",
        plan_tier=plan_tier,
        gateway=gateway,
        status="pending",
        pix_qr_code=pix_qr_code,
        pix_copy_paste=pix_copy_paste,
        expires_at=expires_at,
        created_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def confirm_payment_order(
    db: Session,
    order: PaymentOrder,
    gateway_payment_id: str | None = None,
) -> PaymentOrder:
    """Mark an order as paid and upgrade the associated user to PRO."""
    if order.status == "paid":
        return order

    order.status = "paid"
    order.paid_at = now_iso()
    if gateway_payment_id:
        order.gateway_payment_id = gateway_payment_id

    # Upgrade the user
    user = order.user
    if user:
        user.plan_tier = order.plan_tier
        user.updated_at = now_iso()

    db.commit()
    db.refresh(order)
    log.info("payment.order_confirmed", order_id=order.order_id, user_id=order.user_id, plan=order.plan_tier)
    return order


def get_order_by_id(db: Session, order_id: str) -> PaymentOrder | None:
    return db.query(PaymentOrder).filter(PaymentOrder.order_id == order_id).first()
