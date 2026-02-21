# db.py
import re
import urllib.parse
import sys
import traceback
import os
import asyncpg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    # For tests you may not set DATABASE_URL; don't raise during import, only when connecting.
    DATABASE_URL = None

_pool: asyncpg.pool.Pool | None = None

def _normalize_for_asyncpg(dsn: str) -> str:
    """
    Convert SQLAlchemy-style DSN (e.g. postgresql+asyncpg://...) to a plain
    asyncpg-compatible DSN (postgresql://...).
    """
    if not dsn:
        return dsn
    # If URL contains '+', remove the '+driver' part in the scheme only:
    # e.g. postgresql+asyncpg://user:pass@host/db -> postgresql://user:pass@host/db
    m = re.match(r"(?P<scheme>[^:\/]+)\+(?P<driver>[^:\/]+)(?P<rest>://.*)$", dsn)
    if m:
        return m.group("scheme") + m.group("rest")
    return dsn

async def get_pool():
    """
    Return an asyncpg pool. Creates it on first use.
    """
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL is not configured in environment")
        dsn = _normalize_for_asyncpg(DATABASE_URL)
        # optional: print masked DSN for debug
        print("DEBUG: asyncpg DSN (masked):", (dsn[:60] + "...") if dsn else None)
        # Provide options for SSL if needed by your RDS. Example commented below.
        # import ssl
        # ssl_ctx = ssl.create_default_context(cafile="/path/to/rds-combined-ca-bundle.pem")
        # _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=10, ssl=ssl_ctx)
        _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=10)
    return _pool


async def init_db():
    """
    Initialize DB schema required by the app. Safe to call at startup.
    """
    # If DATABASE_URL not set, do nothing (useful for unit tests that mock get_pool)
    if not DATABASE_URL:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Create creators table using 'creator_id' as the unique identifier (text)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS creators (
            id serial PRIMARY KEY,
            creator_id text UNIQUE NOT NULL,
            user_type text,
            first_name text,
            last_name text,
            email text,
            time_creation timestamptz DEFAULT now(),
            clerk_id text, -- keep clerk_id if you want it as a separate column
            created_at timestamptz DEFAULT now()
        );
        """)
        # Keep existing clerk_users table if you still need it:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS clerk_users (
            id serial PRIMARY KEY,
            clerk_id text UNIQUE NOT NULL,
            created_at timestamptz DEFAULT now()
        );
        """)


async def upsert_creator(creator_id: str, email: str | None = None, display_name: str | None = None,
                         first_name: str | None = None, last_name: str | None = None, user_type: str | None = None):
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured; cannot upsert creator")
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            res = await conn.fetchrow(
                """
                INSERT INTO creators (creator_id, clerk_id, email, first_name, last_name, user_type)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (creator_id) DO UPDATE SET
                  clerk_id = COALESCE(EXCLUDED.clerk_id, creators.clerk_id),
                  email = COALESCE(EXCLUDED.email, creators.email),
                  first_name = COALESCE(EXCLUDED.first_name, creators.first_name),
                  last_name = COALESCE(EXCLUDED.last_name, creators.last_name),
                  user_type = COALESCE(EXCLUDED.user_type, creators.user_type)
                RETURNING creator_id, clerk_id;
                """,
                creator_id, creator_id, email, first_name, last_name, user_type
            )
            # If RETURNING returns None (older PG behavior on DO NOTHING), do a SELECT fallback:
            if not res:
                res = await conn.fetchrow("SELECT creator_id, clerk_id FROM creators WHERE creator_id=$1", creator_id)
            print("DEBUG: upsert/result:", res)
            return res
        except Exception:
            print("ERROR: upsert failed", file=sys.stderr)
            traceback.print_exc()
            raise