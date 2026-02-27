import pytest
import asyncio
import os
from app.db import upsert_creator, get_pool

TEST_CREATOR_ID = "user_pytest_abc123"
TEST_EMAIL = "pytest_user@example.com"
TEST_FIRST = "Py"
TEST_LAST = "Test"


@pytest.mark.asyncio
async def test_upsert_minimal_creator():
    # Insert / update
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
    assert db_row["creator_id"] == TEST_CREATOR_ID

    # Cleanup so test is repeatable
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM creators WHERE creator_id = $1",
            TEST_CREATOR_ID,
        )