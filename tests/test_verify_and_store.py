# File: tests/test_verify_and_store.py
import os
import time
import pytest
from fastapi.testclient import TestClient
import psycopg2
from psycopg2.extras import RealDictCursor

# ADJUST: import your FastAPI app and the module where get_current_user is defined.
# Example assumes your FastAPI app instance is `app` in main.py and get_current_user is in app.auth
try:
    from main import app  # ADJUST if your app is in a different module
except Exception as e:
    raise RuntimeError("Unable to import `app` from main.py. Adjust the import in the test file.") from e

# Try importing the module that contains get_current_user so we can monkeypatch it.
# If your app defined get_current_user in a different module, change the import below.
try:
    import app.auth as auth_module  # ADJUST: the module path that contains `get_current_user`
except Exception:
    # fallback: try importing from top-level auth
    try:
        import auth as auth_module
    except Exception:
        auth_module = None  # We'll handle this later

# --- Fake Clerk payload: mimic the fields your endpoint expects ---
FAKE_CLERK_USER = {
    "id": "user_test_1234567890",            # `sub` / clerk id your code expects
    "object": "user",
    "username": "testuser",
    "first_name": "Caye",
    "last_name": "Lee",
    "primary_email_address": "cayelee@usc.edu",  # sometimes Clerk uses primary_email_address
    "email": "cayelee@usc.edu",                 # include both just in case
    "time_creation": time.strftime("%Y-%m-%d %H:%M:%S%z"),
    # any other claims your code expects (e.g., roles, metadata) can be added here
}

# --- Helper to query DB and fetch creators row by clerk id ---
def fetch_creator_row(db_url: str, clerk_user_id: str):
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM creators WHERE creator_id = %s OR clerk_id = %s LIMIT 1",
                (clerk_user_id, clerk_user_id),
            )
            row = cur.fetchone()
            return row
    finally:
        conn.close()

# --- The test that calls the endpoint and checks DB --- 
def test_verify_and_store_inserts_creator(monkeypatch):
    """
    This test overrides the get_current_user dependency to return FAKE_CLERK_USER,
    calls the /api/auth/verify-and-store endpoint and then checks the creators table
    for a row with that clerk id.
    """

    # 1) Override dependency: try to find the dependency function to patch
    patched = False
    if auth_module is not None and hasattr(auth_module, "get_current_user"):
        # If your route does `Depends(get_current_user)` where get_current_user is imported from auth_module,
        # monkeypatching this symbol will make the dependency return our fake payload.
        def fake_get_current_user(*args, **kwargs):
            # your actual get_current_user may return dict or pydantic model; match that
            return FAKE_CLERK_USER

        monkeypatch.setattr(auth_module, "get_current_user", fake_get_current_user)
        patched = True

    # If we couldn't locate auth_module/get_current_user, we try to override dependency via app.dependency_overrides
    if not patched:
        # This is more generic: depends on the object reference used in your route.
        # Set a catch-all override for any dependency that returns a dict.
        app.dependency_overrides = getattr(app, "dependency_overrides", {})
        app.dependency_overrides[lambda: None] = lambda: FAKE_CLERK_USER
        # If your route uses Depends(get_current_user) where get_current_user is the actual function object
        # and you can import it, set app.dependency_overrides[get_current_user] = lambda: FAKE_CLERK_USER
        # (Left generic because module paths differ across projects.)

    client = TestClient(app)

    # 2) Call the endpoint. The endpoint signature had payload: dict = Depends(get_current_user)
    response = client.post("/api/auth/verify-and-store", json={})
    # For debugging, print returned body if not 200
    if response.status_code != 200:
        print("Endpoint response status:", response.status_code)
        print("Response body:", response.text)

    assert response.status_code in (200, 201), "Expected 200/201 from verify-and-store endpoint"

    # Response JSON should contain some debug info (your endpoint returns debug in dev)
    resp_json = response.json()
    # Adjust these checks to match whatever your endpoint returns. Common debug keys: clerk_user_id, upsert_result
    assert (
        "clerk_user_id" in resp_json or "upsert_result" in resp_json or "debug" in resp_json
    ), "Response JSON did not include expected debug keys"

    # 3) Verify DB row exists (requires DATABASE_URL env var)
    db_url = os.getenv("DATABASE_URL")
    assert db_url, "Set DATABASE_URL env var to your AWS Postgres connection string before running tests."

    row = fetch_creator_row(db_url, FAKE_CLERK_USER["id"])
    assert row is not None, f"No row found in creators table for clerk id {FAKE_CLERK_USER['id']}"

    # spot-check some fields returned from DB row to match the fake payload
    # adjust column names according to your creators table (e.g., creator_id, clerk_id, first_name, last_name, email)
    assert (
        row.get("creator_id") == FAKE_CLERK_USER["id"]
        or row.get("clerk_id") == FAKE_CLERK_USER["id"]
    ), "DB row does not have the expected clerk id"

    # Check firstname/lastname/email if columns exist
    if "first_name" in row or "first_name" in row:
        # some schemas use first_name vs first_name / first_name maybe different column; try variants
        first_col = row.get("first_name") or row.get("first_name") or row.get("first")
        last_col = row.get("last_name") or row.get("last_name") or row.get("last")
        if first_col is not None:
            assert first_col == FAKE_CLERK_USER["first_name"]
        if last_col is not None:
            assert last_col == FAKE_CLERK_USER["last_name"]

    # Email
    for email_col in ("email", "primary_email_address", "primary_email"):
        if email_col in row and row[email_col] is not None:
            assert row[email_col] == FAKE_CLERK_USER["email"]
            break

    # Clean up: optionally delete the test row to keep DB tidy.
    # (Only do this if you want test to be idempotent.)
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM creators WHERE creator_id = %s OR clerk_id = %s",
                (FAKE_CLERK_USER["id"], FAKE_CLERK_USER["id"]),
            )
            conn.commit()
    finally:
        conn.close()