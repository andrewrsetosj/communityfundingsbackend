"""
Campaign reports v2 — user-facing report submission endpoint.

POST /api/reports/campaign/{campaign_id}
  Body: { reporter_clerk_id: str, reason: str, notes?: str }
  Inserts into existing cf-db campaign_reports table with full snapshot
  of the campaign at time of report (defends against creator editing later).

Public route (no admin required), but requires reporter_clerk_id (sign-in).
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db

router = APIRouter(prefix="/api/reports", tags=["reports-v2"])


class CampaignReportIn(BaseModel):
    reporter_clerk_id: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1, max_length=200)
    notes: Optional[str] = Field(None, max_length=2000)


@router.post("/campaign/{campaign_id}")
async def submit_campaign_report(
    campaign_id: int,
    data: CampaignReportIn,
    db: AsyncSession = Depends(get_db),
):
    """
    Submit a report against a campaign.
    Captures snapshot of the campaign at time of report.
    Prevents duplicate reports from same user against same campaign.
    """

    # Verify reporter exists in creators table
    rep = await db.execute(
        text("SELECT creator_id FROM creators WHERE creator_id = :cid"),
        {"cid": data.reporter_clerk_id},
    )
    if not rep.first():
        raise HTTPException(status_code=401, detail="Sign in to report a campaign")

    # Verify campaign exists + load snapshot data
    c = await db.execute(text("""
        SELECT c.campaign_id, c.creator_id, c.title, c.status, c.url,
               c.description_html, c.category, c.location,
               c.funding_goal_cents, c.duration_days, c.amount_raised_cents,
               c.backers, c.time_created, c.end_date, c.bio,
               cr.name AS creator_name, cr.last_name AS creator_last_name,
               cr.username AS creator_username, cr.user_type AS creator_user_type
          FROM campaigns c
          LEFT JOIN creators cr ON cr.creator_id = c.creator_id
         WHERE c.campaign_id = :cid
    """), {"cid": campaign_id})
    camp = c.mappings().first()
    if not camp:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Self-report guard
    if camp["creator_id"] == data.reporter_clerk_id:
        raise HTTPException(status_code=400, detail="You cannot report your own campaign")

    # Duplicate guard — has this user already submitted an open report?
    existing = await db.execute(text("""
        SELECT report_id FROM campaign_reports
         WHERE reporter_creator_id = :rid
           AND reported_campaign_id = :cid
           AND status = 'open'
    """), {"rid": data.reporter_clerk_id, "cid": campaign_id})
    if existing.first():
        raise HTTPException(status_code=409, detail="You have already reported this campaign")

    # Insert report with full snapshot
    await db.execute(text("""
        INSERT INTO campaign_reports (
            reporter_creator_id,
            reported_campaign_id,
            reported_campaign_creator_id,
            reported_campaign_creator_name,
            reported_campaign_creator_last_name,
            reported_campaign_creator_username,
            reported_campaign_creator_user_type,
            reported_campaign_title_snapshot,
            reported_campaign_status_snapshot,
            reported_campaign_url_snapshot,
            reported_campaign_description_html_snapshot,
            reported_campaign_category_snapshot,
            reported_campaign_location_snapshot,
            reported_campaign_funding_goal_cents_snapshot,
            reported_campaign_duration_days_snapshot,
            reported_campaign_amount_raised_cents_snapshot,
            reported_campaign_backers_snapshot,
            reported_campaign_time_created_snapshot,
            reported_campaign_end_date_snapshot,
            reported_campaign_bio_snapshot,
            reason,
            notes,
            status
        ) VALUES (
            :rid, :cid, :ccid, :cname, :clname, :cuser, :cutype,
            :title, :cstatus, :curl, :cdesc, :ccat, :cloc,
            :cgoal, :cdays, :craised, :cback, :ctime, :cend, :cbio,
            :reason, :notes, 'open'
        )
    """), {
        "rid": data.reporter_clerk_id,
        "cid": campaign_id,
        "ccid": camp["creator_id"],
        "cname": camp["creator_name"],
        "clname": camp["creator_last_name"],
        "cuser": camp["creator_username"],
        "cutype": camp["creator_user_type"],
        "title": camp["title"],
        "cstatus": camp["status"],
        "curl": camp["url"],
        "cdesc": camp["description_html"],
        "ccat": camp["category"],
        "cloc": camp["location"],
        "cgoal": camp["funding_goal_cents"],
        "cdays": camp["duration_days"],
        "craised": camp["amount_raised_cents"],
        "cback": camp["backers"],
        "ctime": camp["time_created"],
        "cend": camp["end_date"],
        "cbio": camp["bio"],
        "reason": data.reason,
        "notes": data.notes,
    })

    await db.commit()
    return {"status": "reported", "campaign_id": campaign_id}
