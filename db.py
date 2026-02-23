# db.py
import re
import sys
import datetime
import traceback
import os
import asyncpg
from dotenv import load_dotenv
from typing import Optional, Any

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    DATABASE_URL = None

_pool: Optional[asyncpg.pool.Pool] = None


def _normalize_for_asyncpg(dsn: Optional[str]) -> Optional[str]:
    if not dsn:
        return dsn
    m = re.match(r"(?P<scheme>[^:\/]+)\+(?P<driver>[^:\/]+)(?P<rest>://.*)$", dsn)
    if m:
        return m.group("scheme") + m.group("rest")
    return dsn


async def get_pool() -> asyncpg.pool.Pool:
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


async def init_db():
    """
    Initialize DB schema required by the app. Safe to call at startup.
    """
    if not DATABASE_URL:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS creators (
                id serial PRIMARY KEY,
                creator_id text UNIQUE NOT NULL,
                user_type text,
                first_name text,
                last_name text,
                email text,
                time_creation timestamptz DEFAULT now(),
                created_at timestamptz DEFAULT now()
            );
            """
        )

# db.py -> replace the previous upsert_creator with this function

from typing import Optional, Any
import datetime
import traceback
import sys

async def upsert_creator(
    creator_id: str,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    email: Optional[str] = None,
    time_creation: Optional[datetime.datetime] = None,
) -> Any:
    """
    Upsert a creators row using creator_id as the unique key.
    Only writes: creator_id, first_name, last_name, email, time_creation.
    Does NOT touch is_business (leaves DB default as-is).
    """
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured; cannot upsert creator")

    # If caller didn't provide time_creation, let DB set NOW() by passing NULL
    # We can pass Python datetime if provided.
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            if time_creation is None:
                # Use NOW() on insert (pass NULL for the parameter)
                row = await conn.fetchrow(
                    """
                    INSERT INTO creators (creator_id, first_name, last_name, email, time_creation)
                    VALUES ($1, $2, $3, $4, NOW())
                    ON CONFLICT (creator_id) DO UPDATE SET
                      first_name = COALESCE(EXCLUDED.first_name, creators.first_name),
                      last_name  = COALESCE(EXCLUDED.last_name, creators.last_name),
                      email      = COALESCE(EXCLUDED.email, creators.email)
                    RETURNING *;
                    """,
                    creator_id,
                    first_name,
                    last_name,
                    email,
                )
            else:
                # If time_creation provided, pass it as a parameter
                row = await conn.fetchrow(
                    """
                    INSERT INTO creators (creator_id, first_name, last_name, email, time_creation)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (creator_id) DO UPDATE SET
                      first_name = COALESCE(EXCLUDED.first_name, creators.first_name),
                      last_name  = COALESCE(EXCLUDED.last_name, creators.last_name),
                      email      = COALESCE(EXCLUDED.email, creators.email),
                      time_creation = COALESCE(EXCLUDED.time_creation, creators.time_creation)
                    RETURNING *;
                    """,
                    creator_id,
                    first_name,
                    last_name,
                    email,
                    time_creation,
                )

            if not row:
                row = await conn.fetchrow("SELECT * FROM creators WHERE creator_id = $1 LIMIT 1", creator_id)

            # Optional debug print
            print("DEBUG: upsert_creator returned:", dict(row) if row else None)
            return row

        except Exception:
            print("ERROR: upsert_creator failed", file=sys.stderr)
            traceback.print_exc()
            raise


# Optional helper to explicitly close the pool (useful in tests)
async def close_pool():
    global _pool
    if _pool is not None:
        try:
            await _pool.close()
        finally:
            _pool = None