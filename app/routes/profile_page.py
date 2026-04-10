from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import get_current_user, get_optional_user
from app.db import get_pool
from app.models.models import User

router = APIRouter(prefix="/api/profile-page", tags=["profile-page"])


class ReportProfileRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=2000)


class UpdatePinnedCampaignRequest(BaseModel):
    campaign_id: int | None = None


async def _get_creator_profile(conn, creator_id_or_username: str) -> dict | None:
    creator = await conn.fetchrow(
        """
        SELECT
            creator_id,
            COALESCE(NULLIF(username, ''), creator_id) AS username,
            user_type,
            name,
            last_name,
            bio,
            website,
            avatar_url,
            email,
            time_creation,
            pinned_campaign_id
        FROM creators
        WHERE creator_id = $1
           OR LOWER(COALESCE(username, '')) = LOWER($1)
        LIMIT 1
        """,
        creator_id_or_username,
    )
    return dict(creator) if creator else None


@router.get("/{creator_id_or_username}")
async def get_profile_page(creator_id_or_username: str, current_user: User | None = Depends(get_optional_user)):
    pool = await get_pool()

    async with pool.acquire() as conn:
        creator = await _get_creator_profile(conn, creator_id_or_username)
        if not creator:
            raise HTTPException(status_code=404, detail="Profile not found")

        creator_id = creator["creator_id"]
        creator_email = (creator.get("email") or "").strip()
        viewer_is_self = bool(current_user and current_user.id == creator_id)

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
                campaign["image_url"] = f"https://{campaign['s3_bucket']}.s3.us-east-2.amazonaws.com/{campaign['s3_key']}"
            else:
                campaign["image_url"] = None

        collaborations = await conn.fetch(
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
                cp.is_primary,
                owner.name AS owner_name,
                owner.last_name AS owner_last_name,
                COALESCE(NULLIF(owner.username, ''), owner.creator_id) AS owner_username
            FROM collaborators coll
            JOIN campaigns c
              ON c.campaign_id = coll.campaign_id
            JOIN creators owner
              ON owner.creator_id = c.creator_id
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
            WHERE LOWER(coll.email) = LOWER($1)
              AND LOWER(COALESCE(coll.status, '')) = 'accepted'
              AND c.status <> 'inactive'
              AND ($2::bool OR c.status = 'active')
            ORDER BY coll.time_created DESC, c.campaign_id DESC
            """,
            creator_email,
            viewer_is_self,
        )
        collaborations = [dict(c) for c in collaborations]
        for campaign in collaborations:
            if campaign.get("s3_bucket") and campaign.get("s3_key"):
                campaign["image_url"] = f"https://{campaign['s3_bucket']}.s3.us-east-2.amazonaws.com/{campaign['s3_key']}"
            else:
                campaign["image_url"] = None

        activity_rows = await conn.fetch(
            """
            SELECT *
            FROM (
                SELECT
                    'commented'::text AS activity_type,
                    c.time_created AS activity_time,
                    c.comment_id,
                    c.comment_text AS activity_text,
                    camp.campaign_id,
                    camp.url AS campaign_url,
                    camp.title AS campaign_title,
                    camp.status AS campaign_status,
                    NULL::text AS target_creator_id,
                    NULL::text AS target_name,
                    NULL::text AS target_last_name,
                    NULL::text AS target_username
                FROM comments c
                JOIN campaigns camp
                  ON camp.campaign_id = c.campaign_id
                WHERE c.creator_id = $1

                UNION ALL

                SELECT
                    'followed'::text AS activity_type,
                    cf.time_created AS activity_time,
                    NULL::bigint AS comment_id,
                    NULL::text AS activity_text,
                    NULL::bigint AS campaign_id,
                    NULL::text AS campaign_url,
                    NULL::text AS campaign_title,
                    NULL::text AS campaign_status,
                    followed.creator_id AS target_creator_id,
                    followed.name AS target_name,
                    followed.last_name AS target_last_name,
                    COALESCE(NULLIF(followed.username, ''), followed.creator_id) AS target_username
                FROM creator_follows cf
                JOIN creators followed
                  ON followed.creator_id = cf.followed_creator_id
                WHERE cf.follower_creator_id = $1

                UNION ALL

                SELECT
                    'joined_as_collaborator'::text AS activity_type,
                    coll.time_created AS activity_time,
                    NULL::bigint AS comment_id,
                    NULL::text AS activity_text,
                    camp.campaign_id,
                    camp.url AS campaign_url,
                    camp.title AS campaign_title,
                    camp.status AS campaign_status,
                    NULL::text AS target_creator_id,
                    NULL::text AS target_name,
                    NULL::text AS target_last_name,
                    NULL::text AS target_username
                FROM collaborators coll
                JOIN campaigns camp
                  ON camp.campaign_id = coll.campaign_id
                WHERE LOWER(coll.email) = LOWER($2)
                  AND LOWER(COALESCE(coll.status, '')) = 'accepted'

                UNION ALL

                SELECT
                    'created_campaign'::text AS activity_type,
                    camp.time_created AS activity_time,
                    NULL::bigint AS comment_id,
                    NULL::text AS activity_text,
                    camp.campaign_id,
                    camp.url AS campaign_url,
                    camp.title AS campaign_title,
                    camp.status AS campaign_status,
                    NULL::text AS target_creator_id,
                    NULL::text AS target_name,
                    NULL::text AS target_last_name,
                    NULL::text AS target_username
                FROM campaigns camp
                WHERE camp.creator_id = $1
            ) activity
            ORDER BY activity_time DESC
            LIMIT 10
            """,
            creator_id,
            creator_email,
        )

        activities = []
        for row in activity_rows:
            item = dict(row)
            item["activity_text_preview"] = item["activity_text"][:180] if item.get("activity_text") else None
            activities.append(item)

    return {
        "creator": creator,
        "interests": interests,
        "campaigns": campaigns,
        "collaborations": collaborations,
        "activities": activities,
    }


@router.put("/me/pinned-campaign")
async def update_pinned_campaign(
    payload: UpdatePinnedCampaignRequest,
    current_user: User = Depends(get_current_user),
):
    pool = await get_pool()

    async with pool.acquire() as conn:
        if payload.campaign_id is None:
            await conn.execute(
                """
                UPDATE creators
                SET pinned_campaign_id = NULL
                WHERE creator_id = $1
                """,
                current_user.id,
            )
            return {"ok": True, "pinned_campaign_id": None}

        campaign = await conn.fetchrow(
            """
            SELECT campaign_id, creator_id, status
            FROM campaigns
            WHERE campaign_id = $1
            LIMIT 1
            """,
            payload.campaign_id,
        )

        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")

        if campaign["creator_id"] != current_user.id:
            raise HTTPException(status_code=403, detail="You can only pin your own campaigns")

        if (campaign["status"] or "").lower() != "active":
            raise HTTPException(status_code=400, detail="Only active campaigns can be pinned")

        await conn.execute(
            """
            UPDATE creators
            SET pinned_campaign_id = $2
            WHERE creator_id = $1
            """,
            current_user.id,
            payload.campaign_id,
        )

    return {"ok": True, "pinned_campaign_id": payload.campaign_id}


@router.post("/{creator_id_or_username}/report")
async def report_profile(
    creator_id_or_username: str,
    payload: ReportProfileRequest,
    current_user: User = Depends(get_current_user),
):
    pool = await get_pool()

    async with pool.acquire() as conn:
        creator = await _get_creator_profile(conn, creator_id_or_username)
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
            creator["username"],
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
        "creator_id": creator["creator_id"],
        "username": creator["username"],
        "status": "open",
        "time_reported": inserted["time_reported"],
    }