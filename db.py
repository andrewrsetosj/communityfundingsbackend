# db.py
import os
import asyncio
import asyncpg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

_pool = None

async def get_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
    return _pool

async def init_db():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS clerk_users (
            id serial PRIMARY KEY,
            clerk_id text UNIQUE NOT NULL,
            created_at timestamptz DEFAULT now()
        );
        """)
