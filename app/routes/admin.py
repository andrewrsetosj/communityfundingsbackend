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
    """Platform-wide statistics using raw SQL for RDS compatibility."""
    from sqlalchemy import text
    r = await db.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM creators) as users,
            (SELECT COUNT(*) FROM campaigns) as campaigns,
            (SELECT COUNT(*) FROM campaigns WHERE status='active') as active_campaigns,
            (SELECT COALESCE(SUM(amount),0) FROM donations WHERE status='succeeded') as total_raised,
            (SELECT COUNT(*) FROM donations WHERE status='succeeded') as total_donations,
            (SELECT COUNT(*) FROM reports) as open_reports,
            0 as pending_refunds
    """))
    row = r.mappings().first()
    return {
        "users": row["users"],
        "campaigns": row["campaigns"],
        "active_campaigns": row["active_campaigns"],
        "total_raised": float(row["total_raised"]),
        "total_donations": row["total_donations"],
        "open_reports": row["open_reports"],
        "pending_refunds": row["pending_refunds"],
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

# ── Delete campaign (archive to deleted_campaigns) ─────────────────────────

@router.post("/campaigns/{campaign_id}/delete")
async def delete_campaign(
    campaign_id: str,
    reason: str = "Admin removal",
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete a campaign by archiving it to deleted_campaigns, then removing from campaigns."""
    from sqlalchemy import text
    cid = int(campaign_id) if campaign_id.isdigit() else 0
    if not cid:
        raise HTTPException(status_code=400, detail="Invalid campaign ID")

    # Check campaign exists
    result = await db.execute(select(Campaign).where(Campaign.id == cid))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Archive to deleted_campaigns
    await db.execute(text("""
        INSERT INTO deleted_campaigns (campaign_id, creator_id, title, status, description, category, location,
            funding_goal_cents, amount_raised_cents, backers, url, end_date, duration_days, time_created,
            deleted_by, deletion_reason)
        SELECT campaign_id, creator_id, title, status, description, category, location,
            funding_goal_cents, amount_raised_cents, backers, url, end_date, duration_days, time_created,
            :admin_id, :reason
        FROM campaigns WHERE campaign_id = :cid
    """), {"cid": cid, "admin_id": admin.id, "reason": reason})

    # Delete from campaigns
    await db.execute(text("DELETE FROM campaigns WHERE campaign_id = :cid"), {"cid": cid})
    await db.commit()

    return {"status": "deleted", "campaign_id": cid, "reason": reason, "archived": True}


@router.get("/campaigns/deleted")
async def list_deleted_campaigns(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all deleted/archived campaigns."""
    from sqlalchemy import text
    result = await db.execute(text("""
        SELECT campaign_id, title, creator_id, status, category, location,
               funding_goal_cents, amount_raised_cents, backers,
               time_created, deleted_at, deleted_by, deletion_reason
        FROM deleted_campaigns ORDER BY deleted_at DESC
    """))
    rows = result.mappings().all()
    return [{
        "campaign_id": r["campaign_id"], "title": r["title"], "creator_id": r["creator_id"],
        "status": r["status"], "category": r["category"], "location": r["location"],
        "goal": (r["funding_goal_cents"] or 0) / 100, "raised": (r["amount_raised_cents"] or 0) / 100,
        "backers": r["backers"], "created_at": str(r["time_created"]),
        "deleted_at": str(r["deleted_at"]), "deleted_by": r["deleted_by"],
        "reason": r["deletion_reason"],
    } for r in rows]


@router.get("/campaigns/reported")
async def reported_campaigns(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get all campaigns that have been reported."""
    from sqlalchemy import text
    result = await db.execute(text("""
        SELECT DISTINCT c.campaign_id, c.title, c.creator_id, c.status, c.category, c.location,
               c.funding_goal_cents, c.amount_raised_cents, c.backers, c.time_created,
               cr.name as creator_name,
               (SELECT COUNT(*) FROM reports r WHERE r.campaign_id = c.campaign_id) as report_count
        FROM campaigns c
        JOIN reports r ON r.campaign_id = c.campaign_id
        LEFT JOIN creators cr ON cr.creator_id = c.creator_id
        ORDER BY report_count DESC
    """))
    rows = result.mappings().all()
    return [{
        "campaign_id": r["campaign_id"], "title": r["title"],
        "creator_id": r["creator_id"], "creator_name": r["creator_name"],
        "status": r["status"], "category": r["category"],
        "goal": (r["funding_goal_cents"] or 0) / 100,
        "raised": (r["amount_raised_cents"] or 0) / 100,
        "backers": r["backers"], "report_count": r["report_count"],
        "created_at": str(r["time_created"]),
    } for r in rows]


@router.post("/campaigns/{campaign_id}/restore")
async def restore_campaign(
    campaign_id: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Restore a deleted campaign from archive."""
    from sqlalchemy import text
    cid = int(campaign_id) if campaign_id.isdigit() else 0
    if not cid:
        raise HTTPException(status_code=400, detail="Invalid campaign ID")
    check = await db.execute(text("SELECT campaign_id FROM deleted_campaigns WHERE campaign_id = :cid"), {"cid": cid})
    if not check.first():
        raise HTTPException(status_code=404, detail="Deleted campaign not found")
    await db.execute(text("""
        INSERT INTO campaigns (campaign_id, creator_id, title, status, description, category, location,
            funding_goal_cents, amount_raised_cents, backers, url, end_date, duration_days, time_created)
        SELECT campaign_id, creator_id, title, 'active', description, category, location,
            funding_goal_cents, amount_raised_cents, backers, url, end_date, duration_days, time_created
        FROM deleted_campaigns WHERE campaign_id = :cid
    """), {"cid": cid})
    await db.execute(text("DELETE FROM deleted_campaigns WHERE campaign_id = :cid"), {"cid": cid})
    await db.commit()
    return {"status": "restored", "campaign_id": cid}
