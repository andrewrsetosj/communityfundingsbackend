from fastapi import APIRouter, HTTPException
from app.db import get_pool

router = APIRouter(prefix="/api/campaign-page", tags=["campaign-page"])


@router.get("/{campaign_url}")
async def get_campaign_page(campaign_url: str):
    """
    Campaign details endpoint that accepts either an integer ID or a slug.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        # 1) Campaign — try by integer ID first, then by slug
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

        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")

        campaign = dict(campaign)
        cid = campaign["campaign_id"]

        # 2) Creator (owner of campaign)
        creator = await conn.fetchrow(
            """
            SELECT *
            FROM creators
            WHERE creator_id = $1
            """,
            campaign["creator_id"],
        )
        creator = dict(creator) if creator else None

        # 3) FAQs
        faqs = await conn.fetch(
            """
            SELECT *
            FROM faqs
            WHERE campaign_id = $1
            ORDER BY display_order ASC
            """,
            cid,
        )
        faqs = [dict(f) for f in faqs]

        # 4) Rewards
        rewards = await conn.fetch(
            """
            SELECT *
            FROM rewards
            WHERE campaign_id = $1
            ORDER BY display_order ASC, reward_id ASC
            """,
            cid,
        )
        rewards = [dict(r) for r in rewards]

        # 5) Photos
        photos = await conn.fetch(
            """
            SELECT *
            FROM campaign_photos
            WHERE campaign_id = $1
            ORDER BY is_primary DESC, photo_id ASC
            """,
            cid,
        )
        photos = [dict(p) for p in photos]

        for p in photos:
            p["image_url"] = (
                f"https://{p['s3_bucket']}.s3.us-east-2.amazonaws.com/"
                f"{p['s3_key']}"
            )

        # 6) Comments
        comments = await conn.fetch(
            """
            SELECT
                c.comment_id,
                c.comment_text,
                c.creator_id,
                c.campaign_id,
                c.time_created,
                cr.name,
                cr.last_name
            FROM comments c
            LEFT JOIN creators cr ON cr.creator_id = c.creator_id
            WHERE c.campaign_id = $1
            ORDER BY c.time_created DESC, c.comment_id DESC
            """,
            cid,
        )
        comments = [dict(c) for c in comments]

    return {
        "campaign": campaign,
        "creator": creator,
        "faqs": faqs,
        "rewards": rewards,
        "photos": photos,
        "comments": comments,
    }