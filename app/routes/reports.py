"""
Report routes — users can flag campaigns or comments for review
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.auth import get_current_user
from app.models.models import Report, ReportReason, User
from app.models.schemas import ReportCreate, ReportResponse

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.post("", response_model=ReportResponse, status_code=201)
async def create_report(
    data: ReportCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Flag a campaign or comment for admin review."""
    if not data.campaign_id and not data.comment_id:
        raise HTTPException(status_code=400, detail="Must specify campaign_id or comment_id")

    try:
        reason_enum = ReportReason(data.reason)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid reason. Use: {[r.value for r in ReportReason]}")

    report = Report(
        reporter_id=user.id,
        campaign_id=data.campaign_id,
        comment_id=data.comment_id,
        reason=reason_enum,
        details=data.details,
    )
    db.add(report)
    await db.flush()

    return ReportResponse(
        id=report.id, campaign_id=report.campaign_id,
        comment_id=report.comment_id,
        reason=report.reason.value,
        status="open", created_at=report.created_at,
    )