from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import get_current_user
from app.db import get_pool
from app.models.models import User

router = APIRouter(prefix="/api/profile-page", tags=["profile-page"])


class ReportProfileRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=2000)


async def _get_creator_profile(conn, creator_id: str) -> dict | None:
    creator = await conn.fetchrow(
        """
        SELECT
            creator_id,
            user_type,
            name,
            last_name,
            bio,
            website,
            avatar_url,
            time_creation
        FROM creators
        WHERE creator_id = $1
        """,
        creator_id,
    )

    return dict(creator) if creator else None


@router.get("/{creator_id}")
async def get_profile_page(creator_id: str):
    pool = await get_pool()

    async with pool.acquire() as conn:
        creator = await _get_creator_profile(conn, creator_id)
        if not creator:
            raise HTTPException(status_code=404, detail="Profile not found")

        interests = await conn.fetch(
            """
            SELECT i.name
            FROM creator_interests ci
            JOIN interests i
              ON i.interest_id = ci.interest_id
            WHERE ci.creator_id = $1
            ORDER BY i.name ASC
            """,
            creator_id,
        )
        interests = [row["name"] for row in interests]

        campaigns = await conn.fetch(
            """
            SELECT
                c.campaign_id,
                c.creator_id,
                c.title,
                c.status,
                c.time_created,
                c.url,
                c.description_html,
                c.category,
                c.location,
                c.funding_goal_cents,
                c.duration_days,
                c.amount_raised_cents,
                c.backers,
                cp.photo_id,
                cp.s3_bucket,
                cp.s3_key,
                cp.content_type,
                cp.is_primary
            FROM campaigns c
            LEFT JOIN LATERAL (
                SELECT
                    photo_id,
                    s3_bucket,
                    s3_key,
                    content_type,
                    is_primary
                FROM campaign_photos
                WHERE campaign_id = c.campaign_id
                ORDER BY is_primary DESC, photo_id ASC
                LIMIT 1
            ) cp ON true
            WHERE c.creator_id = $1
            ORDER BY c.time_created DESC, c.campaign_id DESC
            """,
            creator_id,
        )

        campaigns = [dict(c) for c in campaigns]
        for campaign in campaigns:
            if campaign.get("s3_bucket") and campaign.get("s3_key"):
                campaign["image_url"] = (
                    f"https://{campaign['s3_bucket']}.s3.us-east-2.amazonaws.com/"
                    f"{campaign['s3_key']}"
                )
            else:
                campaign["image_url"] = None

        activity_rows = await conn.fetch(
    """
    SELECT *
    FROM (
        -- Comments
        SELECT
            'commented'::text AS activity_type,
            c.time_created AS activity_time,
            c.comment_id,
            c.comment_text AS activity_text,
            c.campaign_id,
            camp.url AS campaign_url,
            camp.title AS campaign_title,
            NULL::text AS target_creator_id,
            NULL::text AS target_name,
            NULL::text AS target_last_name
        FROM comments c
        JOIN campaigns camp
          ON camp.campaign_id = c.campaign_id
        WHERE c.creator_id = $1

        UNION ALL

        -- Follows
        SELECT
            'followed'::text AS activity_type,
            cf.time_created AS activity_time,
            NULL::bigint AS comment_id,
            NULL::text AS activity_text,
            NULL::bigint AS campaign_id,
            NULL::text AS campaign_url,
            NULL::text AS campaign_title,
            cf.followed_creator_id AS target_creator_id,
            cr.name AS target_name,
            cr.last_name AS target_last_name
        FROM creator_follows cf
        JOIN creators cr
          ON cr.creator_id = cf.followed_creator_id
        WHERE cf.follower_creator_id = $1

        UNION ALL

        -- Campaigns created
        SELECT
            'created_campaign'::text AS activity_type,
            c.time_created AS activity_time,
            NULL::bigint AS comment_id,
            NULL::text AS activity_text,
            c.campaign_id,
            c.url AS campaign_url,
            c.title AS campaign_title,
            NULL::text AS target_creator_id,
            NULL::text AS target_name,
            NULL::text AS target_last_name
        FROM campaigns c
        WHERE c.creator_id = $1
    ) activity
    ORDER BY activity_time DESC
    LIMIT 10
    """,
    creator_id,
)

        activities = []
        for row in activity_rows:
            item = dict(row)
            if item.get("activity_text"):
                item["activity_text_preview"] = item["activity_text"][:180]
            else:
                item["activity_text_preview"] = None
            activities.append(item)

    return {
        "creator": creator,
        "interests": interests,
        "campaigns": campaigns,
        "activities": activities,
    }

@router.post("/{creator_id}/report")
async def report_profile(
    creator_id: str,
    payload: ReportProfileRequest,
    current_user: User = Depends(get_current_user),
):
    pool = await get_pool()

    async with pool.acquire() as conn:
        creator = await conn.fetchrow(
            """
            SELECT
                creator_id,
                user_type,
                name,
                last_name,
                bio,
                website,
                avatar_url,
                time_creation
            FROM creators
            WHERE creator_id = $1
            """,
            creator_id,
        )

        if not creator:
            raise HTTPException(status_code=404, detail="Profile not found")

        inserted = await conn.fetchrow(
            """
            INSERT INTO profile_reports (
                reporter_creator_id,
                reported_profile_creator_id,
                reported_profile_user_type,
                reported_profile_name,
                reported_profile_last_name,
                reported_profile_username,
                reported_profile_bio_snapshot,
                reported_profile_website_snapshot,
                reported_profile_avatar_url_snapshot,
                reported_profile_time_creation_snapshot,
                reason,
                notes,
                status
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NULLIF($11, ''), NULLIF($12, ''), 'open'
            )
            RETURNING report_id, time_reported
            """,
            current_user.id,
            creator["creator_id"],
            creator["user_type"],
            creator["name"],
            creator["last_name"],
            creator["creator_id"],
            creator["bio"],
            creator["website"],
            creator["avatar_url"],
            creator["time_creation"],
            (payload.reason or "").strip(),
            (payload.notes or "").strip(),
        )

    return {
        "ok": True,
        "report_id": inserted["report_id"],
        "creator_id": creator_id,
        "status": "open",
        "time_reported": inserted["time_reported"],
    }

