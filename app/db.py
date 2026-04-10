# app/db.py
"""
AsyncPG helper module for simple creators upserts and minimal schema init.

- Uses asyncpg connection pool.
- init_db() only ensures the `creators` table exists (safe for dev).
- Provides upsert_creator(...) to insert or update a creator by creator_id.
"""

from __future__ import annotations
import os
import re
import sys
import traceback
import datetime
from typing import Optional, Any

import asyncpg
from dotenv import load_dotenv

load_dotenv()

# Read DB URL from environment (expected to be postgresql+asyncpg://... or postgresql://...)
DATABASE_URL: Optional[str] = os.getenv("DATABASE_URL") or None

_pool: Optional[asyncpg.pool.Pool] = None


def _normalize_for_asyncpg(dsn: Optional[str]) -> Optional[str]:
    """
    Convert SQLAlchemy-style 'postgresql+asyncpg://...' into 'postgresql://...' (asyncpg accepts either).
    Returns the original dsn if no change needed.
    """
    if not dsn:
        return dsn
    m = re.match(r"(?P<scheme>[^:\/]+)\+(?P<driver>[^:\/]+)(?P<rest>://.*)$", dsn)
    if m:
        return f"{m.group('scheme')}{m.group('rest')}"
    return dsn


async def get_pool() -> asyncpg.pool.Pool:
    """
    Lazily create and return an asyncpg pool.
    Raises RuntimeError if DATABASE_URL is not configured.
    """
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL is not configured in environment")
        dsn = _normalize_for_asyncpg(DATABASE_URL)
        # Mask DSN in logs so password isn't exposed
        try:
            masked = re.sub(r":\/\/(.*@)", "://***@", dsn)
        except Exception:
            masked = (dsn[:60] + "...") if dsn else None
        print("DEBUG: asyncpg DSN (masked):", masked)
        _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=10)
    return _pool


async def init_db() -> None:
    """
    Create minimal schema required by the app. Safe to call at startup.
    Only creates the `creators` table (IF NOT EXISTS) to avoid touching other tables.
    """
    if not DATABASE_URL:
        # nothing to do in environments without DB configured
        return
    if DATABASE_URL.startswith("sqlite"):
        print("✅ Using SQLite — skipping asyncpg pool")
        return

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("SET search_path TO public")
        exists = await conn.fetchval("SELECT EXISTS(SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename='creators')")
        if exists:
            print("✅ creators table already exists — skipping CREATE")
        else:
            print("⚠ creators table missing — ask admin to run db/schema.sql")
    print("✅ asyncpg init_db complete")


async def upsert_creator(
    creator_id: str,
    name: Optional[str] = None,
    last_name: Optional[str] = None,
    email: Optional[str] = None,
    time_creation: Optional[datetime.datetime] = None,
) -> Any:
    """
    Insert or update a creators row using creator_id as the unique key.

    Returns the returned row from the DB (asyncpg.Record) or None on failure.
    """
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured; cannot upsert creator")

    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            if time_creation is None:
                row = await conn.fetchrow(
                    """
                    INSERT INTO creators (creator_id, name, last_name, email, time_creation)
                    VALUES ($1, $2, $3, $4, NOW())
                    ON CONFLICT (creator_id) DO UPDATE SET
                      name = COALESCE(EXCLUDED.name, creators.name),
                      last_name  = COALESCE(EXCLUDED.last_name, creators.last_name),
                      email      = COALESCE(EXCLUDED.email, creators.email)
                    RETURNING *;
                    """,
                    creator_id,
                    name,
                    last_name,
                    email,
                )
            else:
                row = await conn.fetchrow(
                    """
                    INSERT INTO creators (creator_id, name, last_name, email, time_creation)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (creator_id) DO UPDATE SET
                      name = COALESCE(EXCLUDED.name, creators.name),
                      last_name  = COALESCE(EXCLUDED.last_name, creators.last_name),
                      email      = COALESCE(EXCLUDED.email, creators.email),
                      time_creation = COALESCE(EXCLUDED.time_creation, creators.time_creation)
                    RETURNING *;
                    """,
                    creator_id,
                    name,
                    last_name,
                    email,
                    time_creation,
                )

            if not row:
                # Fallback read (should rarely be necessary)
                row = await conn.fetchrow(
                    "SELECT * FROM creators WHERE creator_id = $1 LIMIT 1", creator_id
                )

            # convert to dict for easy logging (asyncpg.Record -> dict-like)
            try:
                row_dict = dict(row) if row else None
            except Exception:
                row_dict = None

            print("DEBUG: upsert_creator returned:", row_dict)
            return row

        except Exception:
            print("ERROR: upsert_creator failed", file=sys.stderr)
            traceback.print_exc()
            raise


async def close_pool() -> None:
    """Explicitly close the asyncpg pool (useful for tests / graceful shutdown)."""
    global _pool
    if _pool is not None:
        try:
            await _pool.close()
        finally:
            _pool = None


# what this module exports
__all__ = [
    "DATABASE_URL",
    "get_pool",
    "init_db",
    "upsert_creator",
    "close_pool",
]