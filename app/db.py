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
from pathlib import Path
from typing import Optional, Any

import asyncpg
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_pool: Optional[asyncpg.pool.Pool] = None


def get_database_url() -> Optional[str]:
    """
    Read DATABASE_URL at call time (Railway / Docker set env after image build).
    Strips whitespace and wrapping quotes — common misconfiguration on hosts.
    """
    raw = os.getenv("DATABASE_URL")
    if raw is None:
        return None
    s = raw.strip().strip('"').strip("'")
    if not s:
        return None
    return _normalize_for_asyncpg(s)


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


def _validate_asyncpg_dsn(dsn: str) -> None:
    if "://" not in dsn:
        raise RuntimeError(
            "DATABASE_URL must be a full URL including scheme (e.g. postgresql://user:pass@host:5432/dbname). "
            "Host-only values are not accepted."
        )
    scheme = dsn.split("://", 1)[0].lower()
    if scheme not in ("postgresql", "postgres"):
        raise RuntimeError(
            "DATABASE_URL must start with postgresql:// or postgres://. "
            f"After loading from the environment, the scheme was {scheme!r}. "
            "On Railway, paste the full RDS URL in Variables (no ${{}} unless the referenced variable exists); "
            "remove stray quotes and line breaks."
        )


async def get_pool() -> asyncpg.pool.Pool:
    """
    Lazily create and return an asyncpg pool.
    Raises RuntimeError if DATABASE_URL is not configured.
    """
    global _pool
    if _pool is None:
        dsn = get_database_url()
        if not dsn:
            raise RuntimeError("DATABASE_URL is not configured in environment")
        _validate_asyncpg_dsn(dsn)
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
    dsn = get_database_url()
    if not dsn:
        # nothing to do in environments without DB configured
        return
    if dsn.startswith("sqlite"):
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


async def _creators_first_name_column(conn: asyncpg.Connection) -> str:
    """RDS schemas vary: some use `name`, others `first_name` (see backend/db.py)."""
    rows = await conn.fetch(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'creators'
        """
    )
    colset = {r["column_name"] for r in rows}
    if "name" in colset:
        return "name"
    if "first_name" in colset:
        return "first_name"
    raise RuntimeError(
        "creators table has neither `name` nor `first_name`; cannot upsert. "
        f"Found columns: {sorted(colset)}"
    )


async def upsert_creator(
    creator_id: str,
    name: Optional[str] = None,
    last_name: Optional[str] = None,
    email: Optional[str] = None,
    time_creation: Optional[datetime.datetime] = None,
    user_type: Optional[int] = None,
) -> Any:
    """
    Insert or update a creators row using creator_id as the unique key.

    Returns the returned row from the DB (asyncpg.Record) or None on failure.
    """
    if not get_database_url():
        raise RuntimeError("DATABASE_URL is not configured; cannot upsert creator")

    ut = 0 if user_type is None else int(user_type)

    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            first_col = await _creators_first_name_column(conn)
            has_user_type = await conn.fetchval(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'creators'
                  AND column_name = 'user_type'
                """
            )
            if time_creation is None:
                if has_user_type:
                    row = await conn.fetchrow(
                        f"""
                        INSERT INTO creators (creator_id, {first_col}, last_name, email, user_type, time_creation)
                        VALUES ($1, $2, $3, $4, $5, NOW())
                        ON CONFLICT (creator_id) DO UPDATE SET
                          {first_col} = COALESCE(EXCLUDED.{first_col}, creators.{first_col}),
                          last_name  = COALESCE(EXCLUDED.last_name, creators.last_name),
                          email      = COALESCE(EXCLUDED.email, creators.email),
                          user_type  = COALESCE(EXCLUDED.user_type, creators.user_type)
                        RETURNING *;
                        """,
                        creator_id,
                        name,
                        last_name,
                        email,
                        ut,
                    )
                else:
                    row = await conn.fetchrow(
                        f"""
                        INSERT INTO creators (creator_id, {first_col}, last_name, email, time_creation)
                        VALUES ($1, $2, $3, $4, NOW())
                        ON CONFLICT (creator_id) DO UPDATE SET
                          {first_col} = COALESCE(EXCLUDED.{first_col}, creators.{first_col}),
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
                if has_user_type:
                    row = await conn.fetchrow(
                        f"""
                        INSERT INTO creators (creator_id, {first_col}, last_name, email, user_type, time_creation)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        ON CONFLICT (creator_id) DO UPDATE SET
                          {first_col} = COALESCE(EXCLUDED.{first_col}, creators.{first_col}),
                          last_name  = COALESCE(EXCLUDED.last_name, creators.last_name),
                          email      = COALESCE(EXCLUDED.email, creators.email),
                          user_type  = COALESCE(EXCLUDED.user_type, creators.user_type),
                          time_creation = COALESCE(EXCLUDED.time_creation, creators.time_creation)
                        RETURNING *;
                        """,
                        creator_id,
                        name,
                        last_name,
                        email,
                        ut,
                        time_creation,
                    )
                else:
                    row = await conn.fetchrow(
                        f"""
                        INSERT INTO creators (creator_id, {first_col}, last_name, email, time_creation)
                        VALUES ($1, $2, $3, $4, $5)
                        ON CONFLICT (creator_id) DO UPDATE SET
                          {first_col} = COALESCE(EXCLUDED.{first_col}, creators.{first_col}),
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
    "get_database_url",
    "get_pool",
    "init_db",
    "upsert_creator",
    "close_pool",
]