from fastapi import APIRouter, HTTPException
from app.db import get_pool

router = APIRouter(prefix="/api/campaign-page", tags=["campaign-page"])


@router.get("/{campaign_id}")
async def get_campaign_page(campaign_id: int):
    """
    Campaign details endpoint that matches the DBeaver schema:
    - campaigns
    - creators
    - campaign_faqs
    - campaign_rewards
    - comments
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        # 1) Campaign
        campaign = await conn.fetchrow(
            """
            SELECT *
            FROM campaigns
            WHERE campaign_id = $1
            """,
            campaign_id,
        )
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")

        campaign = dict(campaign)

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
            FROM campaign_faqs
            WHERE campaign_id = $1
            ORDER BY display_order ASC, faq_id ASC
            """,
            campaign_id,
        )
        faqs = [dict(f) for f in faqs]

        # 4) Rewards
        rewards = await conn.fetch(
            """
            SELECT *
            FROM campaign_rewards
            WHERE campaign_id = $1
            ORDER BY display_order ASC, reward_id ASC
            """,
            campaign_id,
        )
        rewards = [dict(r) for r in rewards]

        # 5) Comments (join creators so frontend can show commenter name)
        comments = await conn.fetch(
            """
            SELECT
                c.comment_id,
                c.comment_text,
                c.creator_id,
                c.campaign_id,
                c.time_created,
                cr.first_name,
                cr.last_name
            FROM comments c
            LEFT JOIN creators cr ON cr.creator_id = c.creator_id
            WHERE c.campaign_id = $1
            ORDER BY c.time_created DESC, c.comment_id DESC
            """,
            campaign_id,
        )
        comments = [dict(c) for c in comments]

    return {
        "campaign": campaign,
        "creator": creator,
        "faqs": faqs,
        "rewards": rewards,
        "comments": comments,
    }