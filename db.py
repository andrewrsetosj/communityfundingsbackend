"""
Minimal database layer for campaign APIs.
Uses asyncpg only (no SQLAlchemy startup dependency).
"""

import os
import re
import ssl
from datetime import datetime, timezone, timedelta
from typing import Any, Iterable

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


async def get_platform_stats() -> dict[str, Any]:
    """Get real-time platform stats: total funded campaigns, total raised, total donations."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        funded_count = await conn.fetchval(
            "SELECT COUNT(*) FROM public.campaigns WHERE status = 'active' AND amount_raised_cents > 100"
        ) or 0

        total_raised = await conn.fetchval(
            "SELECT COALESCE(SUM(amount_raised_cents), 0) FROM public.campaigns"
        ) or 0

        total_donations = await conn.fetchval(
            "SELECT COALESCE(SUM(backers), 0) FROM public.campaigns"
        ) or 0

    return {
        "projects_funded": int(funded_count),
        "total_raised_cents": int(total_raised),
        "total_pledges": int(total_donations),
    }


def _campaign_photo_public_url(s3_bucket: str, s3_key: str) -> str:
    region = (os.getenv("AWS_REGION") or "us-east-2").strip()
    return f"https://{s3_bucket}.s3.{region}.amazonaws.com/{s3_key}"


async def replace_campaign_photos_conn(
    conn: asyncpg.Connection,
    campaign_id: int,
    creator_id: str,
    photos: Iterable[dict[str, Any]],
) -> None:
    photo_list = list(photos)

    row = await conn.fetchrow(
        """
        SELECT creator_id, status FROM public.campaigns
        WHERE campaign_id = $1
        LIMIT 1
        """,
        campaign_id,
    )
    if not row:
        raise ValueError("Campaign not found")
    if str(row["creator_id"]) != str(creator_id):
        raise ValueError("Campaign not found or not owned by this user")
    if str(row["status"]) not in ("draft", "pending_review"):
        raise ValueError("Photos can only be updated for draft or pending-review campaigns")

    await conn.execute(
        "DELETE FROM public.campaign_photos WHERE campaign_id = $1",
        campaign_id,
    )

    insert_sql = """
        INSERT INTO public.campaign_photos (
            campaign_id, s3_bucket, s3_key, content_type,
            is_primary, sort_order, uploaded_by_creator_id
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7)
    """

    for i, p in enumerate(photo_list):
        bucket = str((p or {}).get("s3_bucket") or "").strip()
        key = str((p or {}).get("s3_key") or "").strip()
        if not bucket or not key:
            continue
        ctype = str((p or {}).get("content_type") or "image/jpeg").strip() or "image/jpeg"
        is_primary = bool((p or {}).get("is_primary", False))
        sort_order = int((p or {}).get("sort_order", i))
        await conn.execute(
            insert_sql,
            campaign_id,
            bucket,
            key,
            ctype,
            is_primary,
            sort_order,
            creator_id,
        )

    primaries = await conn.fetch(
        """
        SELECT photo_id FROM public.campaign_photos
        WHERE campaign_id = $1 AND is_primary = TRUE
        ORDER BY sort_order, photo_id
        """,
        campaign_id,
    )
    if len(primaries) > 1:
        for extra in primaries[1:]:
            await conn.execute(
                "UPDATE public.campaign_photos SET is_primary = FALSE WHERE photo_id = $1",
                extra["photo_id"],
            )
    rows_left = await conn.fetch(
        "SELECT photo_id FROM public.campaign_photos WHERE campaign_id = $1 ORDER BY sort_order, photo_id",
        campaign_id,
    )
    primaries_after = await conn.fetch(
        """
        SELECT photo_id FROM public.campaign_photos
        WHERE campaign_id = $1 AND is_primary = TRUE
        ORDER BY sort_order, photo_id
        """,
        campaign_id,
    )
    if rows_left and len(primaries_after) == 0:
        await conn.execute(
            "UPDATE public.campaign_photos SET is_primary = TRUE WHERE photo_id = $1",
            rows_left[0]["photo_id"],
        )


async def replace_campaign_photos(
    campaign_id: int,
    creator_id: str,
    photos: Iterable[dict[str, Any]],
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await replace_campaign_photos_conn(conn, campaign_id, creator_id, photos)


async def _upsert_bank_details_encrypted(
    conn: asyncpg.Connection,
    campaign_id: int,
    creator_id: str,
    payment: dict[str, Any],
) -> None:
    """
    Store one row per campaign in public.bank_details.
    The Fernet ciphertext (JSON blob) is stored in column fermat_key.
    Plaintext account_type is stored for simple filtering.
    """
    from app.bank_crypto import encrypt_bank_payload

    row = await conn.fetchrow(
        "SELECT creator_id FROM public.campaigns WHERE campaign_id = $1",
        campaign_id,
    )
    if not row or str(row["creator_id"]) != str(creator_id):
        raise ValueError("Campaign not found or not owned by this user")

    routing = str(payment.get("routing_number") or "").strip()
    account = str(payment.get("account_number") or "").strip()
    if not routing or not account:
        return

    holder = str(payment.get("account_holder_name") or "").strip()
    acct_type = str(payment.get("account_type") or "individual").strip()
    if acct_type not in ("individual", "business"):
        acct_type = "individual"
    contact_email = str(payment.get("contact_email") or "").strip()

    payload = {
        "routing_number": routing,
        "account_number": account,
        "account_holder_name": holder,
        "account_type": acct_type,
        "contact_email": contact_email,
    }
    token = encrypt_bank_payload(payload)

    await conn.execute(
        "DELETE FROM public.bank_details WHERE campaign_id = $1",
        campaign_id,
    )
    try:
        await conn.execute(
            """
            INSERT INTO public.bank_details (campaign_id, fermat_key, account_type)
            VALUES ($1, $2, $3)
            """,
            campaign_id,
            token,
            acct_type,
        )
    except Exception as e:
        err = str(e).lower()
        if "too long" in err or "character varying" in err:
            raise ValueError(
                "Database column fermat_key (or routing_number before migration) is too short "
                "for encrypted bank data (must be TEXT). Run "
                "backend/db/migrations/001_bank_details_ciphertext.sql if needed, then "
                "002_bank_details_drop_account_rename_routing.sql, then retry."
            ) from e
        raise


async def get_bank_details_decrypted(campaign_id: int) -> dict[str, Any] | None:
    """
    Decrypt stored bank details for payouts or admin (requires BANK_ENCRYPTION_KEY).
    Returns None if no row or empty token.
    """
    from app.bank_crypto import decrypt_bank_payload

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT fermat_key
            FROM public.bank_details
            WHERE campaign_id = $1
            """,
            campaign_id,
        )
    if not row or not row["fermat_key"]:
        return None
    return decrypt_bank_payload(str(row["fermat_key"]))


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
    existing_campaign_id = data.get("campaign_id")

    async with pool.acquire() as conn:
        async with conn.transaction():
            await _ensure_creator_exists(conn, creator_id, data.get("bio"))

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

            if existing_campaign_id:
                # Update the existing draft → pending_review
                row = await conn.fetchrow(
                    """
                    UPDATE public.campaigns
                    SET title = $1, status = 'pending_review', url = $2,
                        description_html = $3, category = $4, "location" = $5,
                        funding_goal_cents = $6, duration_days = $7, end_date = $8, bio = $9
                    WHERE campaign_id = $10 AND creator_id = $11
                    RETURNING campaign_id, url
                    """,
                    title, url, description, category, location,
                    funding_goal_cents, duration_days_val, end_date, bio,
                    int(existing_campaign_id), creator_id,
                )
                if not row:
                    raise ValueError("Campaign not found or not owned by this user")
                campaign_id = int(row["campaign_id"])
            else:
                # Fresh insert
                slug_row = await conn.fetchrow(
                    "SELECT 1 FROM public.campaigns WHERE url = $1 LIMIT 1", url,
                )
                if slug_row:
                    url = f"{url}-{creator_id[:8]}"

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
                    creator_id, title or None, "pending_review", url or None,
                    description, category, location,
                    funding_goal_cents, duration_days_val, end_date, bio,
                )
                campaign_id = int(row["campaign_id"])

            # Delete + re-insert related rows
            await conn.execute("DELETE FROM public.faqs WHERE campaign_id = $1", campaign_id)
            await conn.execute("DELETE FROM public.rewards WHERE campaign_id = $1", campaign_id)
            await conn.execute("DELETE FROM public.collaborators WHERE campaign_id = $1", campaign_id)

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
                    "INSERT INTO public.faqs (campaign_id, display_order, question, answer) VALUES ($1, $2, $3, $4)",
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
                    ) VALUES ($1, $2, $3, $4, $5, $6)
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
                    "INSERT INTO public.collaborators (campaign_id, email, status) VALUES ($1, $2, $3)",
                    collaborator_rows,
                )

            if "photos" in data:
                await replace_campaign_photos_conn(
                    conn, campaign_id, creator_id, data.get("photos") or []
                )

            payment = data.get("payment")
            if payment and isinstance(payment, dict):
                await _upsert_bank_details_encrypted(
                    conn, campaign_id, creator_id, payment
                )

    return {"campaign_id": row["campaign_id"], "slug": row["url"] or url}


async def upsert_draft_campaign(data: dict[str, Any]) -> dict[str, Any]:
    """Create or update a draft campaign and its related tables (faqs, rewards, collaborators)."""
    pool = await get_pool()
    creator_id = (data.get("creator_id") or "").strip()
    if not creator_id:
        raise ValueError("creator_id is required")

    title = (data.get("title") or "").strip() or "Untitled Draft"
    url = _slug(data.get("vanity_slug") or title) or _slug(title)

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
    campaign_id = data.get("campaign_id")

    async with pool.acquire() as conn:
        async with conn.transaction():
            await _ensure_creator_exists(conn, creator_id, bio)

            if campaign_id:
                # UPDATE existing draft
                row = await conn.fetchrow(
                    """
                    UPDATE public.campaigns
                    SET title = $1, url = $2, description_html = $3, category = $4,
                        "location" = $5, funding_goal_cents = $6, duration_days = $7,
                        end_date = $8, bio = $9
                    WHERE campaign_id = $10 AND creator_id = $11 AND status = 'draft'
                    RETURNING campaign_id, url
                    """,
                    title, url, description, category, location,
                    funding_goal_cents, duration_days_val, end_date, bio,
                    int(campaign_id), creator_id,
                )
                if not row:
                    raise ValueError("Draft not found or not owned by this user")
                campaign_id = int(row["campaign_id"])
            else:
                # Ensure unique slug
                existing = await conn.fetchrow(
                    "SELECT 1 FROM public.campaigns WHERE url = $1 LIMIT 1", url,
                )
                if existing:
                    url = f"{url}-{creator_id[:8]}"

                row = await conn.fetchrow(
                    """
                    INSERT INTO public.campaigns (
                        creator_id, title, status, time_created, url,
                        description_html, category, "location",
                        funding_goal_cents, duration_days, amount_raised_cents, backers, end_date, bio
                    )
                    VALUES (
                        $1, $2, 'draft', NOW(), $3,
                        $4, $5, $6,
                        $7, $8, 0, 0, $9, $10
                    )
                    RETURNING campaign_id, url
                    """,
                    creator_id, title, url, description, category, location,
                    funding_goal_cents, duration_days_val, end_date, bio,
                )
                campaign_id = int(row["campaign_id"])

            # Delete + re-insert related rows
            await conn.execute("DELETE FROM public.faqs WHERE campaign_id = $1", campaign_id)
            await conn.execute("DELETE FROM public.rewards WHERE campaign_id = $1", campaign_id)
            await conn.execute("DELETE FROM public.collaborators WHERE campaign_id = $1", campaign_id)

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
                    "INSERT INTO public.faqs (campaign_id, display_order, question, answer) VALUES ($1, $2, $3, $4)",
                    faq_rows,
                )

            # Rewards
            rewards = data.get("rewards") or []
            reward_rows: list[tuple[int, str, int, str, int | None, int]] = []
            for idx, r in enumerate(rewards):
                title_val = str((r or {}).get("title") or "").strip()
                desc_val = str((r or {}).get("description") or "").strip()
                amount = int((r or {}).get("required_amount_cents") or 0)
                if not title_val or amount <= 0:
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
                    ) VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    reward_rows,
                )

            # Collaborators
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
                    "INSERT INTO public.collaborators (campaign_id, email, status) VALUES ($1, $2, $3)",
                    collaborator_rows,
                )

    return {"campaign_id": campaign_id, "slug": row["url"] or url}


async def get_draft_campaign(campaign_id: int, creator_id: str) -> dict[str, Any] | None:
    """Fetch a single draft campaign with its related data, shaped for the frontend store."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        campaign = await conn.fetchrow(
            """
            SELECT campaign_id, title, url, description_html, category, "location",
                   funding_goal_cents, duration_days, bio, status, time_created
            FROM public.campaigns
            WHERE campaign_id = $1 AND creator_id = $2 AND status = 'draft'
            """,
            campaign_id, creator_id,
        )
        if not campaign:
            return None

        faqs = await conn.fetch(
            "SELECT question, answer, display_order FROM public.faqs WHERE campaign_id = $1 ORDER BY display_order",
            campaign_id,
        )
        rewards = await conn.fetch(
            """
            SELECT title, required_amount_cents, description, limit_total, display_order
            FROM public.rewards WHERE campaign_id = $1 ORDER BY display_order
            """,
            campaign_id,
        )
        collaborators = await conn.fetch(
            "SELECT email FROM public.collaborators WHERE campaign_id = $1",
            campaign_id,
        )
        photos = await conn.fetch(
            """
            SELECT s3_bucket, s3_key, content_type, is_primary, sort_order
            FROM public.campaign_photos
            WHERE campaign_id = $1
            ORDER BY is_primary DESC, sort_order ASC, photo_id ASC
            """,
            campaign_id,
        )

    photo_list: list[dict[str, Any]] = []
    for p in photos:
        bucket = (p["s3_bucket"] or "").strip()
        key = (p["s3_key"] or "").strip()
        photo_list.append(
            {
                "s3_bucket": bucket,
                "s3_key": key,
                "content_type": p["content_type"] or "image/jpeg",
                "is_primary": bool(p["is_primary"]),
                "sort_order": int(p["sort_order"] or 0),
                "image_url": _campaign_photo_public_url(bucket, key) if bucket and key else None,
            }
        )

    return {
        "campaign_id": campaign["campaign_id"],
        "title": campaign["title"] or "",
        "category": campaign["category"] or "",
        "location": campaign["location"] or "",
        "funding_goal_cents": int(campaign["funding_goal_cents"] or 0),
        "duration_days": campaign["duration_days"] or 0,
        "description_html": campaign["description_html"] or "",
        "bio": campaign["bio"] or "",
        "vanity_slug": campaign["url"] or "",
        "photos": photo_list,
        "rewards": [
            {
                "title": r["title"] or "",
                "required_amount_cents": int(r["required_amount_cents"] or 0),
                "description": r["description"] or "",
                "limit_total": r["limit_total"],
                "display_order": r["display_order"] or 0,
            }
            for r in rewards
        ],
        "faqs": [
            {
                "question": f["question"] or "",
                "answer": f["answer"] or "",
                "display_order": f["display_order"] or 0,
            }
            for f in faqs
        ],
        "co_creators": [{"email": c["email"]} for c in collaborators],
    }


async def list_draft_campaigns(creator_id: str) -> list[dict[str, Any]]:
    """List all draft campaigns for a given creator."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT campaign_id, title, category, time_created, url
            FROM public.campaigns
            WHERE creator_id = $1 AND status = 'draft'
            ORDER BY time_created DESC
            """,
            creator_id,
        )
    return [
        {
            "campaign_id": r["campaign_id"],
            "title": r["title"] or "Untitled Draft",
            "category": r["category"] or "",
            "created_at": r["time_created"].isoformat() if r["time_created"] else None,
            "slug": r["url"] or "",
        }
        for r in rows
    ]
