# cf-backend/tests/test_routes.py
import pytest
import asyncio
from httpx import AsyncClient
from jwt import InvalidTokenError

# Import your FastAPI app
from main import app

# We'll monkeypatch jwt_utils.verify_token and db.get_pool
import jwt_utils
import db as db_module

# Dummy DB connection objects to capture SQL executed
class DummyConn:
    def __init__(self):
        self.executed = []

    async def execute(self, sql, *args):
        self.executed.append((sql.strip(), args or ()))
        # Simulate successful INSERT, return None

class DummyAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False

class DummyPool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return DummyAcquire(self.conn)

@pytest.mark.asyncio
async def test_verify_and_store_success(monkeypatch):
    # Arrange: mock verify_token to return a payload with sub
    import main
    monkeypatch.setattr(jwt_utils, "verify_token", lambda token: {"sub": "user_abc_123"})
    monkeypatch.setattr(main, "verify_token", lambda token: {"sub": "user_abc_123"}, raising=False)


    # Arrange: mock db.get_pool to return our dummy pool & connection
    conn = DummyConn()
    async def fake_get_pool():
        return DummyPool(conn)
    monkeypatch.setattr(db_module, "get_pool", fake_get_pool)

    async with AsyncClient(app=app, base_url="http://testserver") as ac:
        # Act
        headers = {"Authorization": "Bearer faketoken"}
        resp = await ac.post("/api/auth/verify-and-store", headers=headers, json={})

    # Assert
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["clerkUserId"] == "user_abc_123"
    # confirm DB execute recorded the insert with the clerk id
    assert len(conn.executed) == 1
    sql, args = conn.executed[0]
    assert "INSERT INTO CLERK_USERS" in sql.upper()
    assert args[0] == "user_abc_123"

@pytest.mark.asyncio
async def test_missing_authorization_header(monkeypatch):
    # no special patches needed
    async with AsyncClient(app=app, base_url="http://testserver") as ac:
        resp = await ac.post("/api/auth/verify-and-store", json={})
    assert resp.status_code == 401
    assert "Missing Authorization" in resp.json().get("detail", "")

@pytest.mark.asyncio
async def test_invalid_token(monkeypatch):
    # mock verify_token to raise InvalidTokenError
    def fake_verify(token):
        raise InvalidTokenError("bad token")
    monkeypatch.setattr(jwt_utils, "verify_token", fake_verify)

    async with AsyncClient(app=app, base_url="http://testserver") as ac:
        headers = {"Authorization": "Bearer invalid"}
        resp = await ac.post("/api/auth/verify-and-store", headers=headers, json={})
    assert resp.status_code == 401
    assert "Invalid token" in resp.json().get("detail", "")


@pytest.mark.asyncio
async def test_health_db_success(monkeypatch):
    import main

    class DummyConn:
        async def execute(self, sql):
            assert "SELECT 1" in sql

    class DummyAcquire:
        async def __aenter__(self):
            return DummyConn()
        async def __aexit__(self, exc_type, exc, tb):
            return False

    class DummyPool:
        def acquire(self):
            return DummyAcquire()

    async def fake_get_pool():
        return DummyPool()

    monkeypatch.setattr(main, "get_pool", fake_get_pool)

    async with AsyncClient(app=app, base_url="http://testserver") as ac:
        resp = await ac.get("/health/db")

    assert resp.status_code == 200
    assert resp.json()["db"] == "ok"

