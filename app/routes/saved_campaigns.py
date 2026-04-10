from fastapi import APIRouter, Depends, HTTPException

from app.auth import get_current_user
from app.db import get_pool
from app.models.models import User

router = APIRouter(prefix="/api/saved-campaigns", tags=["saved-campaigns"])
SAVED_ENGAGEMENT_TYPE = 1

async def _get_campaign_by_url_or_id(conn, campaign_url: str):
    campaign = None

    if campaign_url.isdigit():
        campaign = await conn.fetchrow(
            """
            SELECT *
            FROM campaigns
            WHERE campaign_id = $1
            """,
            int(campaign_url),
        )

    if not campaign:
        campaign = await conn.fetchrow(
            """
            SELECT *
            FROM campaigns
            WHERE url = $1
            """,
            campaign_url,
        )

    return dict(campaign) if campaign else None


async def _get_viewer_collaborator_status(conn, campaign_id: int, viewer_id: str | None) -> tuple[bool, bool]:
    if not viewer_id:
        return False, False

    viewer = await conn.fetchrow(
        """
        SELECT email
        FROM creators
        WHERE creator_id = $1
        LIMIT 1
        """,
        viewer_id,
    )
    viewer_email = ((viewer["email"] if viewer else None) or "").strip()
    if not viewer_email:
        return False, False

    invite = await conn.fetchrow(
        """
        SELECT LOWER(COALESCE(status, 'pending')) AS status
        FROM collaborators
        WHERE campaign_id = $1
          AND LOWER(email) = LOWER($2)
        ORDER BY collaborator_id DESC
        LIMIT 1
        """,
        campaign_id,
        viewer_email,
    )
    if not invite:
        return False, False

    status = (invite["status"] or "").lower()
    return status == "accepted", status == "pending"


async def _can_view_campaign(conn, campaign: dict, viewer_id: str | None) -> bool:
    if not campaign:
        return False

    is_owner = bool(viewer_id and campaign["creator_id"] == viewer_id)
    is_collaborator, has_pending_invite = await _get_viewer_collaborator_status(
        conn,
        campaign["campaign_id"],
        viewer_id,
    )
    return bool(
        campaign.get("status") == "active"
        or is_owner
        or is_collaborator
        or has_pending_invite
    )


@router.get("")
async def get_saved_campaigns(current_user: User = Depends(get_current_user)):
    pool = await get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                sc.creator_id AS saved_by_creator_id,
                sc.campaign_id,
                sc.engagement_type,
                sc.time_created AS saved_at,

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
                c.end_date,

                COALESCE(NULLIF(cr.username, ''), cr.creator_id) AS creator_username,
                cr.name AS creator_name,
                cr.last_name AS creator_last_name,
                cr.avatar_url AS creator_avatar_url,

                cp.s3_bucket AS primary_photo_bucket,
                cp.s3_key AS primary_photo_key,
                cp.content_type AS primary_photo_content_type
            FROM saved_campaigns sc
            JOIN campaigns c
              ON c.campaign_id = sc.campaign_id
            LEFT JOIN creators cr
              ON cr.creator_id = c.creator_id
            LEFT JOIN LATERAL (
                SELECT s3_bucket, s3_key, content_type
                FROM campaign_photos
                WHERE campaign_id = c.campaign_id
                ORDER BY is_primary DESC, sort_order ASC NULLS LAST, photo_id ASC
                LIMIT 1
            ) cp ON TRUE
            WHERE sc.creator_id = $1
            ORDER BY sc.time_created DESC, sc.campaign_id DESC
            """,
            current_user.id,
        )

        campaigns = []
        for row in rows:
            item = dict(row)

            can_view = await _can_view_campaign(
                conn,
                {
                    "campaign_id": item["campaign_id"],
                    "creator_id": item["creator_id"],
                    "status": item["status"],
                },
                current_user.id,
            )
            if not can_view:
                continue

            image_url = None
            if item.get("primary_photo_bucket") and item.get("primary_photo_key"):
                image_url = f"https://{item['primary_photo_bucket']}.s3.us-east-2.amazonaws.com/{item['primary_photo_key']}"

            campaigns.append(
                {
                    "campaign_id": item["campaign_id"],
                    "creator_id": item["creator_id"],
                    "title": item["title"],
                    "status": item["status"],
                    "time_created": item["time_created"],
                    "url": item["url"],
                    "description_html": item["description_html"],
                    "category": item["category"],
                    "location": item["location"],
                    "funding_goal_cents": item["funding_goal_cents"],
                    "duration_days": item["duration_days"],
                    "amount_raised_cents": item["amount_raised_cents"],
                    "backers": item["backers"],
                    "end_date": item["end_date"],
                    "saved_at": item["saved_at"],
                    "is_saved": True,
                    "image_url": image_url,
                    "creator": {
                        "creator_id": item["creator_id"],
                        "username": item["creator_username"],
                        "name": item["creator_name"],
                        "last_name": item["creator_last_name"],
                        "avatar_url": item["creator_avatar_url"],
                    },
                }
            )

    return {"campaigns": campaigns}


@router.get("/{campaign_url}/status")
async def get_saved_campaign_status(
    campaign_url: str,
    current_user: User = Depends(get_current_user),
):
    pool = await get_pool()

    async with pool.acquire() as conn:
        campaign = await _get_campaign_by_url_or_id(conn, campaign_url)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")

        is_saved = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                FROM saved_campaigns
                WHERE creator_id = $1
                  AND campaign_id = $2
            )
            """,
            current_user.id,
            campaign["campaign_id"],
        )

    return {
        "campaign_id": campaign["campaign_id"],
        "is_saved": bool(is_saved),
    }


@router.post("/{campaign_url}")
async def save_campaign(
    campaign_url: str,
    current_user: User = Depends(get_current_user),
):
    pool = await get_pool()

    async with pool.acquire() as conn:
        campaign = await _get_campaign_by_url_or_id(conn, campaign_url)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")

        can_view = await _can_view_campaign(conn, campaign, current_user.id)
        if not can_view:
            raise HTTPException(status_code=403, detail="You do not have permission to save this campaign")

        existing = await conn.fetchrow(
            """
            SELECT 1
            FROM saved_campaigns
            WHERE creator_id = $1
              AND campaign_id = $2
            LIMIT 1
            """,
            current_user.id,
            campaign["campaign_id"],
        )

        if not existing:
            try:
                await conn.execute(
                    """
                    INSERT INTO saved_campaigns (creator_id, campaign_id, engagement_type, time_created)
                    VALUES ($1, $2, $3, NOW())
                    """,
                    current_user.id,
                    campaign["campaign_id"],
                    SAVED_ENGAGEMENT_TYPE,
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "Could not save campaign. If your saved_campaigns.engagement_type "
                        f"uses a different numeric code than {SAVED_ENGAGEMENT_TYPE}, "
                        "update SAVED_ENGAGEMENT_TYPE in saved_campaign.py."
                    ),
                ) from exc

    return {
        "ok": True,
        "campaign_id": campaign["campaign_id"],
        "is_saved": True,
    }


@router.delete("/{campaign_url}")
async def unsave_campaign(
    campaign_url: str,
    current_user: User = Depends(get_current_user),
):
    pool = await get_pool()

    async with pool.acquire() as conn:
        campaign = await _get_campaign_by_url_or_id(conn, campaign_url)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")

        await conn.execute(
            """
            DELETE FROM saved_campaigns
            WHERE creator_id = $1
              AND campaign_id = $2
            """,
            current_user.id,
            campaign["campaign_id"],
        )

    return {
        "ok": True,
        "campaign_id": campaign["campaign_id"],
        "is_saved": False,
    }
