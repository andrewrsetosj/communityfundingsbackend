"""
Refund routes — donors can request refunds, admins process them
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from decimal import Decimal
from datetime import datetime, timezone
from typing import List
import stripe
import os

from app.database import get_db
from app.auth import get_current_user, require_admin
from app.models.models import (
    Donation, RefundRequest, User,
    DonationStatus, RefundStatus,
)
from app.models.schemas import RefundRequestCreate, RefundRequestResponse

router = APIRouter(prefix="/api/refunds", tags=["refunds"])

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")


@router.post("", response_model=RefundRequestResponse, status_code=201)
async def request_refund(
    data: RefundRequestCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Donor requests a refund for a donation they made."""
    result = await db.execute(select(Donation).where(Donation.id == data.donation_id))
    donation = result.scalar_one_or_none()
    if not donation:
        raise HTTPException(status_code=404, detail="Donation not found")
    if donation.donor_id != user.id:
        raise HTTPException(status_code=403, detail="Not your donation")
    if donation.status not in (DonationStatus.SUCCEEDED,):
        raise HTTPException(status_code=400, detail=f"Cannot refund a {donation.status.value} donation")

    # Check for existing pending refund
    existing = await db.execute(
        select(RefundRequest).where(
            RefundRequest.donation_id == data.donation_id,
            RefundRequest.status == RefundStatus.REQUESTED,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Refund already requested for this donation")

    refund_req = RefundRequest(
        donation_id=data.donation_id,
        requester_id=user.id,
        reason=data.reason,
        amount=Decimal(str(data.amount)) if data.amount else None,
        status=RefundStatus.REQUESTED,
    )
    db.add(refund_req)
    await db.flush()

    return RefundRequestResponse(
        id=refund_req.id, donation_id=refund_req.donation_id,
        reason=refund_req.reason,
        amount=float(refund_req.amount) if refund_req.amount else None,
        status=refund_req.status.value, created_at=refund_req.created_at,
    )


@router.get("/my", response_model=List[RefundRequestResponse])
async def my_refund_requests(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all refund requests made by the authenticated user."""
    result = await db.execute(
        select(RefundRequest)
        .where(RefundRequest.requester_id == user.id)
        .order_by(RefundRequest.created_at.desc())
    )
    return [
        RefundRequestResponse(
            id=r.id, donation_id=r.donation_id, reason=r.reason,
            amount=float(r.amount) if r.amount else None,
            status=r.status.value, admin_notes=r.admin_notes,
            created_at=r.created_at, resolved_at=r.resolved_at,
        )
        for r in result.scalars().all()
    ]


# ── Admin: process refunds ─────────────────────────────────────────────────

@router.post("/{refund_id}/approve")
async def approve_refund(
    refund_id: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin approves and processes a refund via Stripe."""
    result = await db.execute(select(RefundRequest).where(RefundRequest.id == refund_id))
    refund_req = result.scalar_one_or_none()
    if not refund_req:
        raise HTTPException(status_code=404, detail="Refund request not found")
    if refund_req.status != RefundStatus.REQUESTED:
        raise HTTPException(status_code=400, detail="Refund already processed")

    donation_result = await db.execute(select(Donation).where(Donation.id == refund_req.donation_id))
    donation = donation_result.scalar_one_or_none()
    if not donation or not donation.stripe_payment_intent_id:
        raise HTTPException(status_code=400, detail="No payment to refund")

    # Process via Stripe
    refund_params = {"payment_intent": donation.stripe_payment_intent_id}
    if refund_req.amount:
        refund_params["amount"] = int(float(refund_req.amount) * 100)

    try:
        stripe_refund = stripe.Refund.create(**refund_params)
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=f"Stripe error: {str(e)}")

    refund_req.status = RefundStatus.PROCESSED
    refund_req.stripe_refund_id = stripe_refund.id
    refund_req.resolved_at = datetime.now(timezone.utc)

    # Donation status will be updated by the charge.refunded webhook
    await db.flush()
    return {"status": "refund_processed", "stripe_refund_id": stripe_refund.id}


@router.post("/{refund_id}/deny")
async def deny_refund(
    refund_id: str,
    reason: str = "Does not meet refund policy",
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin denies a refund request."""
    result = await db.execute(select(RefundRequest).where(RefundRequest.id == refund_id))
    refund_req = result.scalar_one_or_none()
    if not refund_req:
        raise HTTPException(status_code=404, detail="Refund request not found")

    refund_req.status = RefundStatus.DENIED
    refund_req.admin_notes = reason
    refund_req.resolved_at = datetime.now(timezone.utc)
    await db.flush()
    return {"status": "refund_denied"}