# tests/test_upsert_persist.py
import pytest
import os
from app.db import upsert_creator, get_pool

TEST_CREATOR_ID = "user_pytest_persist_abc123"
TEST_EMAIL = "pytest_persist@example.com"
TEST_FIRST = "Persist"
TEST_LAST = "Row"

@pytest.mark.asyncio
async def test_upsert_persist_creator():
    # Print which DB we are connecting to
    print("Using DATABASE_URL:", os.environ.get("DATABASE_URL"))
    # Insert / update (no cleanup)
    row = await upsert_creator(
        creator_id=TEST_CREATOR_ID,
        first_name=TEST_FIRST,
        last_name=TEST_LAST,
        email=TEST_EMAIL,
    )

    assert row is not None
    assert row["creator_id"] == TEST_CREATOR_ID
    assert row["first_name"] == TEST_FIRST
    assert row["last_name"] == TEST_LAST
    assert row["email"] == TEST_EMAIL

    # Verify directly from DB
    pool = await get_pool()
    async with pool.acquire() as conn:
        db_row = await conn.fetchrow(
            "SELECT * FROM creators WHERE creator_id = $1",
            TEST_CREATOR_ID,
        )
    assert db_row is not None
    # Intentionally do NOT delete the row so you can inspect it manually