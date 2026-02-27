import pytest
from httpx import AsyncClient
from main import app

@pytest.mark.asyncio
async def test_real_db_ping():
    async with AsyncClient(app=app, base_url="http://testserver") as ac:
        resp = await ac.get("/health/db")

    assert resp.status_code == 200
    assert resp.json()["db"] == "ok"
