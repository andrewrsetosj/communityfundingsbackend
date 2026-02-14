"""
Admin routes — campaign review, moderation, reports, user management
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func as sqlfunc
from typing import Optional, List
from datetime import datetime, timezone

from app.database import get_db
from app.auth import require_admin
from app.models.models import (
    Campaign, CampaignStatus, User, Donation, DonationStatus,
    Report, ReportStatus, ReportReason, Comment, RefundRequest, RefundStatus,
)
from app.models.schemas import CampaignResponse, ReportResponse

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ── Dashboard stats ────────────────────────────────────────────────────────

@router.get("/stats")
async def admin_stats(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Platform-wide statistics."""
    users_count = (await db.execute(select(sqlfunc.count(User.id)))).scalar() or 0
    campaigns_count = (await db.execute(select(sqlfunc.count(Campaign.id)))).scalar() or 0
    active_campaigns = (await db.execute(
        select(sqlfunc.count(Campaign.id)).where(Campaign.status == CampaignStatus.ACTIVE)
    )).scalar() or 0
    total_raised = (await db.execute(
        select(sqlfunc.sum(Donation.amount)).where(Donation.status == DonationStatus.SUCCEEDED)
    )).scalar() or 0
    total_donations = (await db.execute(
        select(sqlfunc.count(Donation.id)).where(Donation.status == DonationStatus.SUCCEEDED)
    )).scalar() or 0
    open_reports = (await db.execute(
        select(sqlfunc.count(Report.id)).where(Report.status == ReportStatus.OPEN)
    )).scalar() or 0
    pending_refunds = (await db.execute(
        select(sqlfunc.count(RefundRequest.id)).where(RefundRequest.status == RefundStatus.REQUESTED)
    )).scalar() or 0

    return {
        "users": users_count,
        "campaigns": campaigns_count,
        "active_campaigns": active_campaigns,
        "total_raised": float(total_raised),
        "total_donations": total_donations,
        "open_reports": open_reports,
        "pending_refunds": pending_refunds,
    }


# ── Campaign moderation ───────────────────────────────────────────────────

@router.get("/campaigns/pending-review")
async def pending_campaigns(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Campaign).where(Campaign.status == CampaignStatus.PENDING_REVIEW)
        .order_by(Campaign.created_at.asc())
    )
    campaigns = result.scalars().all()
    return [{"id": c.id, "title": c.title, "creator_id": c.creator_id, "created_at": str(c.created_at)} for c in campaigns]


@router.post("/campaigns/{campaign_id}/approve")
async def approve_campaign(
    campaign_id: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    campaign.status = CampaignStatus.ACTIVE
    await db.flush()
    return {"status": "approved"}


@router.post("/campaigns/{campaign_id}/suspend")
async def suspend_campaign(
    campaign_id: str,
    reason: str = "Policy violation",
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    campaign.status = CampaignStatus.SUSPENDED
    await db.flush()
    return {"status": "suspended", "reason": reason}


@router.post("/campaigns/{campaign_id}/feature")
async def toggle_featured(
    campaign_id: str,
    featured: bool = True,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    campaign.is_featured = featured
    await db.flush()
    return {"is_featured": featured}


# ── Reports ────────────────────────────────────────────────────────────────

@router.get("/reports", response_model=List[ReportResponse])
async def list_reports(
    status: Optional[str] = "open",
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    query = select(Report)
    if status:
        query = query.where(Report.status == status)
    query = query.order_by(Report.created_at.asc())
    result = await db.execute(query)
    return [
        ReportResponse(
            id=r.id, campaign_id=r.campaign_id, comment_id=r.comment_id,
            reason=r.reason.value if isinstance(r.reason, ReportReason) else r.reason,
            status=r.status.value if isinstance(r.status, ReportStatus) else r.status,
            created_at=r.created_at,
        )
        for r in result.scalars().all()
    ]


@router.post("/reports/{report_id}/resolve")
async def resolve_report(
    report_id: str,
    action: str = "dismissed",  # dismissed | hide_comment | suspend_campaign
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Report).where(Report.id == report_id))
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    if action == "hide_comment" and report.comment_id:
        comment_result = await db.execute(select(Comment).where(Comment.id == report.comment_id))
        comment = comment_result.scalar_one_or_none()
        if comment:
            comment.is_hidden = True

    elif action == "suspend_campaign" and report.campaign_id:
        campaign_result = await db.execute(select(Campaign).where(Campaign.id == report.campaign_id))
        campaign = campaign_result.scalar_one_or_none()
        if campaign:
            campaign.status = CampaignStatus.SUSPENDED

    report.status = ReportStatus.RESOLVED
    report.resolved_at = datetime.now(timezone.utc)
    await db.flush()
    return {"status": "resolved", "action": action}


# ── Pending refunds ────────────────────────────────────────────────────────

@router.get("/refunds/pending")
async def pending_refunds(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(RefundRequest).where(RefundRequest.status == RefundStatus.REQUESTED)
        .order_by(RefundRequest.created_at.asc())
    )
    return [
        {
            "id": r.id, "donation_id": r.donation_id,
            "reason": r.reason,
            "amount": float(r.amount) if r.amount else None,
            "created_at": str(r.created_at),
        }
        for r in result.scalars().all()
    ]