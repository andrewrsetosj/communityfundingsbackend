# db.py
import os
import asyncpg
import asyncio
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL env var is required")

_pool = None

async def get_pool():
    global _pool
    if _pool is None:
        # You can pass ssl options here if your RDS requires SSL
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
    return _pool
async def upsert_clerk_user(clerk_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO clerk_users (clerk_id) VALUES ($1)
            ON CONFLICT (clerk_id) DO NOTHING;
        """, clerk_id)
# async def init_db():
#     pool = await get_pool()
#     async with pool.acquire() as conn:
#         await conn.execute("""
#         CREATE TABLE IF NOT EXISTS clerk_users (
#             id serial PRIMARY KEY,
#             clerk_id text UNIQUE NOT NULL,
#             created_at timestamptz DEFAULT now()
#         );
#         """)
