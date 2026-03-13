"""
Minimal database layer for campaign APIs.
Uses asyncpg only (no SQLAlchemy startup dependency).
"""

import os
import re
import ssl
from datetime import datetime, timezone, timedelta
from typing import Any

import asyncpg

_raw = (os.getenv("DATABASE_URL") or "").strip()
# asyncpg expects postgresql://
DATABASE_URL = _raw.replace("postgresql+asyncpg://", "postgresql://", 1) if _raw else ""

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL is not set")
        use_ssl = "rds.amazonaws.com" in DATABASE_URL or os.getenv("DATABASE_SSL", "").lower() in (
            "1",
            "true",
            "yes",
        )
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


def _slug(raw: str) -> str:
    s = (raw or "").strip().lower()
    s = re.sub(r"[^a-z0-9-]", "", s)
    return s or "campaign"


async def check_slug_available(slug: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM public.campaigns WHERE url = $1 LIMIT 1",
            slug.strip().lower(),
        )
        return row is None


async def list_campaigns(
    *,
    status: str | None = None,
    sort: str = "recent",
    per_page: int = 12,
) -> dict[str, Any]:
    pool = await get_pool()
    per_page = max(1, min(per_page, 50))

    async with pool.acquire() as conn:
        where = []
        args: list[Any] = []
        if status:
            args.append(status)
            where.append(f"c.status = ${len(args)}")
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""

        if sort == "most_funded":
            order_sql = "ORDER BY c.amount_raised_cents DESC NULLS LAST, c.time_created DESC"
        else:
            order_sql = "ORDER BY c.time_created DESC"

        args.append(per_page)
        limit_pos = len(args)

        rows = await conn.fetch(
            f"""
            SELECT
              c.campaign_id,
              c.title,
              c.url,
              c.status,
              c.time_created,
              c.funding_goal_cents,
              c.amount_raised_cents,
              c.end_date,
              c.creator_id,
              cr.name AS creator_name
            FROM public.campaigns c
            LEFT JOIN public.creators cr ON cr.creator_id = c.creator_id
            {where_sql}
            {order_sql}
            LIMIT ${limit_pos}
            """,
            *args,
        )

        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM public.campaigns c {where_sql}",
            *args[:-1],
        )

    now = datetime.now(timezone.utc)
    campaigns: list[dict[str, Any]] = []
    for r in rows:
        goal_cents = int(r["funding_goal_cents"] or 0)
        raised_cents = int(r["amount_raised_cents"] or 0)
        funding_percentage = int(round((raised_cents / goal_cents) * 100)) if goal_cents > 0 else 0
        days_left = None
        if r["end_date"] is not None:
            delta = r["end_date"] - now
            days_left = max(0, delta.days)

        campaigns.append(
            {
                "id": str(r["campaign_id"]),
                "title": r["title"],
                "slug": r["url"] or str(r["campaign_id"]),
                "status": r["status"],
                "goal_amount": goal_cents / 100,
                "raised_amount": raised_cents / 100,
                "funding_percentage": funding_percentage,
                "days_left": days_left,
                "creator_name": r["creator_name"],
                "image_url": None,
            }
        )

    return {"campaigns": campaigns, "total": int(total or 0), "per_page": per_page}


async def _ensure_creator_exists(conn: asyncpg.Connection, creator_id: str, bio: str | None) -> None:
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
    pool = await get_pool()
    creator_id = (data.get("creator_id") or "").strip()
    if not creator_id:
        raise ValueError("creator_id is required")

    title = (data.get("title") or "").strip()
    if not title:
        raise ValueError("title is required")

    url = _slug(data.get("vanity_slug") or title) or _slug(title)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _ensure_creator_exists(conn, creator_id, data.get("bio"))

            row = await conn.fetchrow(
                "SELECT 1 FROM public.campaigns WHERE url = $1 LIMIT 1",
                url,
            )
            if row:
                url = f"{url}-{creator_id[:8]}"

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
            bio = (data.get("bio") or "").strip() or None

            row = await conn.fetchrow(
                """
                INSERT INTO public.campaigns (
                    creator_id, title, status, time_created, url,
                    description_html, category, "location",
                    funding_goal_cents, duration_days, amount_raised_cents, backers, end_date, bio
                )
                VALUES (
                    $1, $2, $3, NOW(), $4,
                    $5, $6, $7,
                    $8, $9, 0, 0, $10, $11
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
                bio,
            )
            campaign_id = int(row["campaign_id"])

            # FAQs
            faqs = data.get("faqs") or []
            faq_rows: list[tuple[int, int, str, str]] = []
            for idx, f in enumerate(faqs):
                question = str((f or {}).get("question") or "").strip()
                answer = str((f or {}).get("answer") or "").strip()
                if not question or not answer:
                    continue
                order = int((f or {}).get("display_order", idx))
                faq_rows.append((campaign_id, order, question, answer))
            if faq_rows:
                await conn.executemany(
                    """
                    INSERT INTO public.faqs (campaign_id, display_order, question, answer)
                    VALUES ($1, $2, $3, $4)
                    """,
                    faq_rows,
                )

            # Rewards
            rewards = data.get("rewards") or []
            reward_rows: list[tuple[int, str, int, str, int | None, int]] = []
            for idx, r in enumerate(rewards):
                title_val = str((r or {}).get("title") or "").strip()
                desc_val = str((r or {}).get("description") or "").strip()
                amount = int((r or {}).get("required_amount_cents") or 0)
                if not title_val or not desc_val or amount <= 0:
                    continue
                limit_raw = (r or {}).get("limit_total")
                limit_total = int(limit_raw) if limit_raw not in (None, "", 0) else None
                order = int((r or {}).get("display_order", idx))
                reward_rows.append((campaign_id, title_val[:100], amount, desc_val, limit_total, order))
            if reward_rows:
                await conn.executemany(
                    """
                    INSERT INTO public.rewards (
                        campaign_id, title, required_amount_cents, description, limit_total, display_order
                    )
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    reward_rows,
                )

            # Collaborators (co_creators in draft)
            co_creators = data.get("co_creators") or []
            seen_emails: set[str] = set()
            collaborator_rows: list[tuple[int, str, str]] = []
            for c in co_creators:
                email = str((c or {}).get("email") or "").strip().lower()
                if not email or email in seen_emails:
                    continue
                seen_emails.add(email)
                collaborator_rows.append((campaign_id, email, "pending"))
            if collaborator_rows:
                await conn.executemany(
                    """
                    INSERT INTO public.collaborators (campaign_id, email, status)
                    VALUES ($1, $2, $3)
                    """,
                    collaborator_rows,
                )

    return {"campaign_id": row["campaign_id"], "slug": row["url"] or url}
