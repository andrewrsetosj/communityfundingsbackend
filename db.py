"""
Minimal database layer for campaign finalize.
Uses asyncpg only — no SQLAlchemy, no table creation.
Expects public.campaigns (and public.creators for FK) to already exist.
"""

import os
import re
from datetime import datetime, timezone, timedelta
from typing import Any

import asyncpg
import ssl

# DATABASE_URL e.g. postgresql://user:pass@host:5432/dbname or postgresql+asyncpg://...
_raw = (os.getenv("DATABASE_URL") or "").strip()
# asyncpg expects postgresql:// (no +asyncpg); strip() avoids .env newline/space breaking the password
DATABASE_URL = _raw.replace("postgresql+asyncpg://", "postgresql://", 1) if _raw else ""

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL is not set")
        # AWS RDS and many managed Postgres require SSL ("no encryption" → use ssl=True)
        use_ssl = "rds.amazonaws.com" in DATABASE_URL or os.getenv("DATABASE_SSL", "").lower() in ("1", "true", "yes")
        ssl_context = None
        if use_ssl:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
        _pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=1,
            max_size=5,
            command_timeout=10,
            ssl=ssl_context,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def check_slug_available(slug: str) -> bool:
    """
    Check if a vanity slug is available (not taken by any campaign).
    Returns True if available, False if taken.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            'SELECT 1 FROM public.campaigns WHERE url = $1 LIMIT 1',
            slug.strip().lower(),
        )
        return row is None


def _slug(raw: str) -> str:
    s = (raw or "").strip().lower()
    s = re.sub(r"[^a-z0-9-]", "", s)
    return s or "campaign"


async def _ensure_creator_exists(
    conn: asyncpg.Connection,
    creator_id: str,
    bio: str | None,
) -> None:
    """
    Insert creator row if missing.
    Supports both known creators table shapes:
    - (creator_id, user_type, name, last_name, email, time_creation)
    - (creator_id, first_name, last_name, email, time_creation, is_business, bio)
    """
    exists = await conn.fetchrow(
        "SELECT 1 FROM public.creators WHERE creator_id = $1 LIMIT 1",
        creator_id,
    )
    if exists:
        return

    placeholder_email = f"{creator_id}@communityfundings.placeholder"
    display_name = (bio or "").strip()[:200] or "Creator"

    cols = await conn.fetch(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'creators'
        """
    )
    colset = {r["column_name"] for r in cols}

    # Build an insert statement based on available columns.
    insert_cols: list[str] = ["creator_id"]
    values: list[Any] = [creator_id]

    if "user_type" in colset:
        insert_cols.append("user_type")
        values.append(1)
    if "name" in colset:
        insert_cols.append("name")
        values.append(display_name)
    if "first_name" in colset:
        insert_cols.append("first_name")
        values.append(display_name)
    if "last_name" in colset:
        insert_cols.append("last_name")
        values.append("")
    if "email" in colset:
        insert_cols.append("email")
        values.append(placeholder_email)
    if "is_business" in colset:
        insert_cols.append("is_business")
        values.append(False)
    if "bio" in colset:
        insert_cols.append("bio")
        values.append((bio or "").strip() or None)

    placeholders = ", ".join(f"${i}" for i in range(1, len(insert_cols) + 1))
    columns_sql = ", ".join(insert_cols)
    sql = (
        f"INSERT INTO public.creators ({columns_sql}) "
        f"VALUES ({placeholders}) "
        "ON CONFLICT (creator_id) DO NOTHING"
    )
    await conn.execute(sql, *values)


async def finalize_campaign(data: dict[str, Any]) -> dict[str, Any]:
    """
    Insert one row into public.campaigns (your DDL).
    Returns {"campaign_id": int, "slug": str}.
    creator_id must exist in public.creators(creator_id) unless we create it.
    """
    pool = await get_pool()
    creator_id = (data.get("creator_id") or "").strip()
    if not creator_id:
        raise ValueError("creator_id is required")

    title = (data.get("title") or "").strip()
    if not title:
        raise ValueError("title is required")

    # Unique url (vanity slug)
    url = _slug(data.get("vanity_slug") or title) or _slug(title)
    async with pool.acquire() as conn:
        await _ensure_creator_exists(conn, creator_id, data.get("bio"))

        # Ensure url unique: if taken, append suffix
        row = await conn.fetchrow(
            'SELECT 1 FROM public.campaigns WHERE url = $1 LIMIT 1',
            url,
        )
        if row:
            url = f"{url}-{creator_id[:8]}"

        # end_date from duration_days
        duration_days = data.get("duration_days")
        end_date = None
        if duration_days and int(duration_days) > 0:
            end_date = datetime.now(timezone.utc) + timedelta(days=int(duration_days))

        funding_goal_cents = int(data.get("funding_goal_cents") or 0)
        if funding_goal_cents < 0:
            funding_goal_cents = 0

        description = (data.get("description_html") or "").strip() or None
        category = (data.get("category") or "").strip() or None
        location = (data.get("location") or "").strip() or None
        duration_days_val = int(duration_days) if duration_days else None

        try:
            row = await conn.fetchrow(
                """
                INSERT INTO public.campaigns (
                    creator_id, title, status, time_created, url,
                    description, category, "location",
                    funding_goal_cents, duration_days, amount_raised_cents, backers, end_date
                )
                VALUES (
                    $1, $2, $3, NOW(), $4,
                    $5, $6, $7,
                    $8, $9, 0, 0, $10
                )
                RETURNING campaign_id, url
                """,
                creator_id,
                title or None,
                "pending_review",
                url or None,
                description,
                category,
                location,
                funding_goal_cents,
                duration_days_val,
                end_date,
            )
        except asyncpg.ForeignKeyViolationError as e:
            raise ValueError(
                "creator_id must exist in public.creators(creator_id). "
                "Create the creator first or add a row to creators."
            ) from e

    return {
        "campaign_id": row["campaign_id"],
        "slug": row["url"] or url,
    }
