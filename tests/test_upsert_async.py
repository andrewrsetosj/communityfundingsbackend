# test_upsert_async.py
import asyncio
from db import upsert_creator, get_pool

async def main():
    r = await upsert_creator("user_test_manual_123", email="x@y.com")
    print("manual upsert returned:", r)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM creators WHERE creator_id='user_test_manual_123'")
        print("row found:", row)

asyncio.run(main())