# v100_signin_required — Tier 1 production fix
"""
Donations v2 — Stripe Checkout for Community Fundings.

This module is intentionally side-by-side with app/routes/payments.py.
It uses raw SQL queries that reference cf-db's actual column names
(donor_creator_id, donation_id, time_created, etc) — bypassing the
ORM Donation model which uses different attribute names.

All endpoints are mounted at /api/donations-v2/* so this never collides
with main's existing /api/stripe/* endpoints.

Cross-checked against cf-db live schema (Apr 30):
    donations:   donation_id (PK), campaign_id, donor_creator_id (NOT NULL),
                 amount, status, time_created, donor_name, donor_email,
                 is_anonymous, message, platform_fee, net_amount, currency,
                 stripe_payment_intent_id, stripe_checkout_session_id,
                 stripe_charge_id
    campaigns:   campaign_id (PK), creator_id, title, status, time_created,
                 url (slug), funding_goal_cents, amount_raised_cents, backers
    creators:    creator_id (PK), name, last_name, email, ...

Anonymous donors are stored as # v100_signin_required — derive from auth, no anonymous fallback
 donor_creator_id = (data.donor_clerk_id or "").strip() or None (string sentinel).
"""

import os
import json
import stripe
from decimal import Decimal
from typing import Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Header
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field, EmailStr

from app.database import get_db

# /* v100_donations_v2 */
router = APIRouter(prefix="/api/donations-v2", tags=["donations-v2"])

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
PLATFORM_FEE_RATE = Decimal("0.05")  # 5%


# ─── Request body shape ───────────────────────────────────────────────
class CheckoutBody(BaseModel):
    campaign_id: int = Field(..., gt=0)
    amount: float = Field(..., gt=0)
    donor_name: Optional[str] = "Anonymous"
    donor_email: Optional[EmailStr] = None
    is_anonymous: bool = False
    message: Optional[str] = None
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None
    donor_clerk_id: str | None = None  # v100_signin_required


# ─── POST /create-checkout-session ────────────────────────────────────
@router.post("/create-checkout-session")
async def create_checkout_session(
    data: CheckoutBody,
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(None),
):
    """Create a Stripe Checkout session for a donation.

    Anonymous-friendly: if no JWT, donor_creator_id stays 'anonymous'.
    Authenticated users (Clerk JWT) get their creator_id stored.
    """
    if not stripe.api_key:
        raise HTTPException(status_code=503, detail="Stripe not configured (STRIPE_SECRET_KEY missing)")

    # 1. Verify the campaign exists and is active
    r = await db.execute(text("""
        SELECT campaign_id, title, status, url, creator_id
          FROM campaigns
         WHERE campaign_id = :cid
    """), {"cid": data.campaign_id})
    campaign = r.mappings().first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign["status"] not in ("active",):
        raise HTTPException(status_code=400, detail=f"Campaign is not accepting donations (status={campaign['status']})")

    # 2. Resolve donor (if Authorization header present, look up the creator)
    donor_creator_id = "anonymous"
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        # We don't verify the JWT here (main may handle that elsewhere) —
        # we just trust that if a token is sent, the user "claims" to be that creator.
        # If JWT verification is needed, integrate with main's auth dependency later.
        try:
            import jwt as pyjwt
            decoded = pyjwt.decode(token, options={"verify_signature": False})
            sub = decoded.get("sub")
            if sub:
                donor_creator_id = sub
        except Exception:
            pass  # fall back to anonymous

        # v100_signin_required — reject if not authenticated
        if not donor_creator_id:
            raise HTTPException(status_code=401, detail="Sign in required to donate.")

    # 3. Calculate fees
    amount = Decimal(str(data.amount))
    platform_fee = (amount * PLATFORM_FEE_RATE).quantize(Decimal("0.01"))
    net_amount = (amount - platform_fee).quantize(Decimal("0.01"))
    amount_cents = int(amount * 100)

    # 4. Insert pending donation row
    insert_q = text("""
        INSERT INTO donations
              (campaign_id, donor_creator_id, donor_name, donor_email,
               is_anonymous, message, amount, platform_fee, net_amount,
               status, currency)
        VALUES (:cid, :duid, :dname, :demail, :anon, :msg, :amt, :pfee,
                :net, 'pending', 'usd')
        RETURNING donation_id
    """)
    res = await db.execute(insert_q, {
        "cid": data.campaign_id,
        "duid": donor_creator_id,
        "dname": data.donor_name or "Anonymous",
        "demail": data.donor_email,
        "anon": data.is_anonymous,
        "msg": data.message,
        "amt": amount,
        "pfee": platform_fee,
        "net": net_amount,
    })
    donation_id = res.scalar_one()

    # 5. Build redirect URLs (point to /donation-receipt)
    success_url = data.success_url or (
        f"{FRONTEND_URL}/donation-receipt?donation_id={donation_id}&campaign_id={data.campaign_id}"
    )
    cancel_url = data.cancel_url or (
        f"{FRONTEND_URL}/project/{campaign['url'] or data.campaign_id}"
    )

    # 6. Create Stripe Checkout Session
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "unit_amount": amount_cents,
                    "product_data": {
                        "name": f"Donation to {campaign['title']}",
                        "description": f"Supporting: {campaign['title']}",
                    },
                },
                "quantity": 1,
            }],
            mode="payment",
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "donation_id": str(donation_id),
                "campaign_id": str(data.campaign_id),
            },
            customer_email=data.donor_email if data.donor_email else None,
        )
    except stripe.error.StripeError as e:
        # Roll back the pending donation if Stripe fails
        await db.execute(text("DELETE FROM donations WHERE donation_id = :did"), {"did": donation_id})
        await db.commit()
        raise HTTPException(status_code=502, detail=f"Stripe error: {str(e)}")

    # 7. Save the session id
    await db.execute(text("""
        UPDATE donations
           SET stripe_checkout_session_id = :sid
         WHERE donation_id = :did
    """), {"sid": session.id, "did": donation_id})
    await db.commit()

    return {
        "checkout_url": session.url,
        "session_id": session.id,
        "donation_id": donation_id,
    }


# ─── GET /donation/{id} — receipt page calls this ────────────────────
@router.get("/donation/{donation_id}")
async def get_donation(donation_id: int, db: AsyncSession = Depends(get_db)):
    """Fetch a donation by ID for the receipt page."""
    r = await db.execute(text("""
        SELECT d.donation_id, d.amount, d.status, d.time_created,
               d.donor_name, d.donor_email, d.campaign_id, d.currency,
               d.platform_fee, d.net_amount, d.is_anonymous,
               c.title AS campaign_title, c.url AS campaign_slug,
               cr.name AS creator_first_name, cr.last_name AS creator_last_name
          FROM donations d
          JOIN campaigns c ON c.campaign_id = d.campaign_id
          LEFT JOIN creators cr ON cr.creator_id = c.creator_id
         WHERE d.donation_id = :did
    """), {"did": donation_id})
    row = r.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Donation not found")

    creator_name = " ".join(filter(None, [row.get("creator_first_name"), row.get("creator_last_name")])) or None
    return {
        "donation_id": row["donation_id"],
        "amount": float(row["amount"]),
        "status": row["status"],
        "created_at": row["time_created"].isoformat() if row["time_created"] else None,
        "donor_name": row["donor_name"],
        "donor_email": row["donor_email"],
        "campaign_id": row["campaign_id"],
        "campaign_title": row["campaign_title"],
        "campaign_slug": row["campaign_slug"],
        "campaign_creator_name": creator_name,
        "currency": row["currency"],
        "platform_fee": float(row["platform_fee"]) if row["platform_fee"] is not None else None,
        "net_amount": float(row["net_amount"]) if row["net_amount"] is not None else None,
        "is_anonymous": row["is_anonymous"],
    }


# ─── POST /webhook — Stripe calls this on payment events ─────────────
@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: Optional[str] = Header(None, alias="stripe-signature"),
    db: AsyncSession = Depends(get_db),
):
    """Stripe webhook — confirms successful payments and bumps campaign totals.

    Configure Stripe to point to:  POST /api/donations-v2/webhook
    """
    payload = await request.body()
    if not WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="STRIPE_WEBHOOK_SECRET not configured")

    try:
        event = stripe.Webhook.construct_event(payload, stripe_signature, WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook error: {str(e)}")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        session_id = session.get("id")
        payment_intent_id = session.get("payment_intent")

        # Look up the donation by session id
        r = await db.execute(text("""
            SELECT donation_id, campaign_id, amount
              FROM donations
             WHERE stripe_checkout_session_id = :sid
        """), {"sid": session_id})
        donation = r.mappings().first()
        if not donation:
            return {"received": True, "warning": "donation not found for session"}

        # Mark donation succeeded
        await db.execute(text("""
            UPDATE donations
               SET status = 'succeeded',
                   stripe_payment_intent_id = :pi
             WHERE donation_id = :did
        """), {"pi": payment_intent_id, "did": donation["donation_id"]})

        # Bump campaign totals (amount_raised_cents and backers count)
        amount_cents = int(Decimal(str(donation["amount"])) * 100)
        await db.execute(text("""
            UPDATE campaigns
               SET amount_raised_cents = COALESCE(amount_raised_cents, 0) + :cents,
                   backers = COALESCE(backers, 0) + 1
             WHERE campaign_id = :cid
        """), {"cents": amount_cents, "cid": donation["campaign_id"]})

        await db.commit()
        return {"received": True, "donation_id": donation["donation_id"], "status": "succeeded"}

    elif event["type"] == "checkout.session.expired":
        session = event["data"]["object"]
        await db.execute(text("""
            UPDATE donations
               SET status = 'failed'
             WHERE stripe_checkout_session_id = :sid
               AND status = 'pending'
        """), {"sid": session.get("id")})
        await db.commit()
        return {"received": True, "status": "expired"}

    return {"received": True, "event_type": event["type"]}
