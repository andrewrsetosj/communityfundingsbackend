"""
Location autocomplete — distinct campaign.location and user.state values
"""

from fastapi import APIRouter, Query
from typing import Optional
import db as db_mod

router = APIRouter(prefix="/api/locations", tags=["locations"])


@router.get("")
async def suggest_locations(
    q: Optional[str] = None,
    limit: int = Query(default=10, ge=1, le=25),
):
    """Return distinct locations from campaigns and users that match the query prefix."""
    pool = await db_mod.get_pool()

    prefix_pattern = f"{(q or '').strip()}%"

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT location
            FROM (
                SELECT DISTINCT TRIM(location) AS location
                FROM campaigns
                WHERE location IS NOT NULL AND TRIM(location) <> ''
                UNION
                SELECT DISTINCT TRIM(state) AS location
                FROM creators
                WHERE state IS NOT NULL AND TRIM(state) <> ''
            ) combined
            WHERE location ILIKE $1
            ORDER BY location
            LIMIT $2
            """,
            prefix_pattern,
            limit,
        )

    return {"locations": [r["location"] for r in rows]}
