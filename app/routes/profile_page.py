from fastapi import APIRouter, HTTPException
from app.db import get_pool

router = APIRouter(prefix="/api/profile-page", tags=["profile-page"])


@router.get("/{creator_id}")
async def get_profile_page(creator_id: str):
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
                time_creation
            FROM creators
            WHERE creator_id = $1
            """,
            creator_id,
        )

        if not creator:
            raise HTTPException(status_code=404, detail="Profile not found")

        creator = dict(creator)

        campaigns = await conn.fetch(
            """
            SELECT
                c.campaign_id,
                c.creator_id,
                c.title,
                c.status,
                c.time_created,
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

    return {
        "creator": creator,
        "campaigns": campaigns,
    }