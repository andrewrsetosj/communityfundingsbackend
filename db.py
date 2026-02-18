# db.py
import os
import asyncpg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    # For tests you may not set DATABASE_URL; don't raise during import, only when connecting.
    DATABASE_URL = None

_pool: asyncpg.pool.Pool | None = None

async def get_pool():
    """
    Return an asyncpg pool. Creates it on first use.
    """
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL is not configured in environment")
        # Provide options for SSL if needed by your RDS. Example:
        # import ssl
        # ssl_ctx = ssl.create_default_context(cafile="/path/to/rds-combined-ca-bundle.pem")
        # _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10, ssl=ssl_ctx)
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
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
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS clerk_users (
            id serial PRIMARY KEY,
            clerk_id text UNIQUE NOT NULL,
            created_at timestamptz DEFAULT now()
        );
        """)

async def upsert_clerk_user(clerk_id: str):
    """
    Idempotently ensure a clerk user row exists.
    """
    # If DATABASE_URL is not set, raise or silently return? We choose to raise so caller knows.
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured; cannot upsert clerk user")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO clerk_users (clerk_id)
            VALUES ($1)
            ON CONFLICT (clerk_id) DO NOTHING;
        """, clerk_id)
