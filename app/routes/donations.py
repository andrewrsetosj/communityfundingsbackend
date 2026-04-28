from fastapi import APIRouter, Depends
from app.auth import get_current_user
from app.db import get_pool
from app.models.models import User

router = APIRouter(prefix="/api/donations", tags=["donations"])


@router.get("/my-backed-campaigns")
async def get_my_backed_campaigns(current_user: User = Depends(get_current_user)):
    pool = await get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (c.campaign_id)
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
                d.time_created AS backed_at,
                creator.creator_id AS creator_creator_id,
                COALESCE(NULLIF(creator.username, ''), creator.creator_id) AS creator_username,
                creator.name AS creator_name,
                creator.last_name AS creator_last_name,
                creator.avatar_url AS creator_avatar_url
            FROM donations d
            JOIN campaigns c
              ON c.campaign_id = d.campaign_id
            LEFT JOIN creators creator
              ON creator.creator_id = c.creator_id
            WHERE d.donor_creator_id = $1
            ORDER BY c.campaign_id, d.time_created DESC
            """,
            current_user.id,
        )

    campaigns = []
    for row in rows:
        item = dict(row)
        item["creator"] = {
            "creator_id": item.pop("creator_creator_id", None),
            "username": item.pop("creator_username", None),
            "name": item.pop("creator_name", None),
            "last_name": item.pop("creator_last_name", None),
            "avatar_url": item.pop("creator_avatar_url", None),
        }
        item["is_saved"] = False
        campaigns.append(item)

    return {"campaigns": campaigns}