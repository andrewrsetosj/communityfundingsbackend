"""
Ledger — donation history for donors and campaign creators

Endpoints:
  GET /api/ledger/donor          — my donation history (auth required)
  GET /api/ledger/creator        — donations received on my campaigns (auth required)
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func as sqlfunc, text
from typing import List

from app.database import get_db
from app.auth import get_current_user
from app.models.models import Donation, Campaign, User

router = APIRouter(prefix="/api/ledger", tags=["ledger"])


@router.get("/donor")
async def donor_ledger(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Get all donations made by the current user, with campaign info."""
    result = await db.execute(
        text("""
            SELECT d.donation_id, d.amount, d.status, d.time_created, d.currency,
                   d.platform_fee, d.net_amount, d.campaign_id,
                   c.title as campaign_title, c.url as campaign_slug,
                   c.creator_id as campaign_creator_id,
                   cr.name as campaign_creator_name
            FROM donations d
            JOIN campaigns c ON c.campaign_id = d.campaign_id
            LEFT JOIN creators cr ON cr.creator_id = c.creator_id
            WHERE d.donor_creator_id = :uid
            ORDER BY d.time_created DESC
        """),
        {"uid": user.id},
    )
    rows = result.mappings().all()

    donations = []
    grand_total = 0.0
    for r in rows:
        amt = float(r["amount"]) if r["amount"] else 0
        grand_total += amt
        donations.append({
            "donation_id": r["donation_id"],
            "amount": amt,
            "status": r["status"],
            "currency": r.get("currency", "usd"),
            "platform_fee": float(r["platform_fee"]) if r["platform_fee"] else 0,
            "net_amount": float(r["net_amount"]) if r["net_amount"] else 0,
            "created_at": str(r["time_created"]),
            "campaign_id": r["campaign_id"],
            "campaign_title": r["campaign_title"],
            "campaign_slug": r["campaign_slug"],
            "campaign_creator_name": r["campaign_creator_name"],
        })

    return {
        "user_id": user.id,
        "user_name": user.name,
        "donations": donations,
        "total_donated": round(grand_total, 2),
        "donation_count": len([d for d in donations if d["status"] == "succeeded"]),
    }


@router.get("/creator")
async def creator_ledger(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Get all donations received on the current user's campaigns."""
    result = await db.execute(
        text("""
            SELECT d.donation_id, d.amount, d.status, d.time_created, d.currency,
                   d.platform_fee, d.net_amount, d.donor_name, d.donor_email,
                   d.is_anonymous, d.campaign_id,
                   c.title as campaign_title, c.url as campaign_slug
            FROM donations d
            JOIN campaigns c ON c.campaign_id = d.campaign_id
            WHERE c.creator_id = :uid
            ORDER BY d.time_created DESC
        """),
        {"uid": user.id},
    )
    rows = result.mappings().all()

    donations = []
    grand_total = 0.0
    total_fees = 0.0
    total_net = 0.0
    for r in rows:
        amt = float(r["amount"]) if r["amount"] else 0
        fee = float(r["platform_fee"]) if r["platform_fee"] else 0
        net = float(r["net_amount"]) if r["net_amount"] else 0
        if r["status"] == "succeeded":
            grand_total += amt
            total_fees += fee
            total_net += net
        donations.append({
            "donation_id": r["donation_id"],
            "amount": amt,
            "status": r["status"],
            "currency": r.get("currency", "usd"),
            "platform_fee": fee,
            "net_amount": net,
            "donor_name": "Anonymous" if r["is_anonymous"] else r["donor_name"],
            "donor_email": None if r["is_anonymous"] else r["donor_email"],
            "created_at": str(r["time_created"]),
            "campaign_id": r["campaign_id"],
            "campaign_title": r["campaign_title"],
        })

    # Campaign summary
    camp_result = await db.execute(
        text("""
            SELECT c.campaign_id, c.title, c.funding_goal_cents, c.amount_raised_cents, c.backers
            FROM campaigns c WHERE c.creator_id = :uid ORDER BY c.campaign_id
        """),
        {"uid": user.id},
    )
    camps = camp_result.mappings().all()
    campaign_summary = [{
        "campaign_id": c["campaign_id"],
        "title": c["title"],
        "goal": (c["funding_goal_cents"] or 0) / 100,
        "raised": (c["amount_raised_cents"] or 0) / 100,
        "backers": c["backers"] or 0,
    } for c in camps]

    return {
        "user_id": user.id,
        "user_name": user.name,
        "donations_received": donations,
        "total_received": round(grand_total, 2),
        "total_fees": round(total_fees, 2),
        "total_net": round(total_net, 2),
        "donation_count": len([d for d in donations if d["status"] == "succeeded"]),
        "campaigns": campaign_summary,
    }
