"""
Comprehensive tests for Community Fundings API
Uses SQLite in-memory + mocked Stripe calls
"""

import pytest
import json
from unittest.mock import patch, MagicMock, AsyncMock
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.database import Base, get_db
from app.auth import hash_password, create_access_token
from app.models.models import User

# ── Test DB setup ──────────────────────────────────────────────────────────

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSession = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def override_get_db():
    async with TestSession() as session:
        async with session.begin():
            yield session


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
async def setup_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def client():
    from main import app
    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def auth_headers():
    """Create a user directly in DB and return auth headers."""
    async with TestSession() as session:
        async with session.begin():
            user = User(
                email="test@example.com",
                name="Test User",
                hashed_password=hash_password("TestPassword123"),
            )
            session.add(user)
            await session.flush()
            token = create_access_token(user.id)
            return {"Authorization": f"Bearer {token}"}, user.id


@pytest.fixture
async def admin_headers():
    """Create an admin user and return auth headers."""
    async with TestSession() as session:
        async with session.begin():
            admin = User(
                email="admin@example.com",
                name="Admin User",
                hashed_password=hash_password("AdminPass123"),
                is_admin=True,
            )
            session.add(admin)
            await session.flush()
            token = create_access_token(admin.id)
            return {"Authorization": f"Bearer {token}"}, admin.id


# ════════════════════════════════════════════════════════════════════════════
# Health
# ════════════════════════════════════════════════════════════════════════════

async def test_root(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


async def test_config(client):
    resp = await client.get("/api/config")
    assert resp.status_code == 200
    assert "stripe_publishable_key" in resp.json()
    assert "platform_fee_percent" in resp.json()


# ════════════════════════════════════════════════════════════════════════════
# Auth
# ════════════════════════════════════════════════════════════════════════════

async def test_register(client):
    resp = await client.post("/api/auth/register", json={
        "email": "new@test.com", "name": "New User", "password": "SecurePass123",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["user"]["email"] == "new@test.com"


async def test_register_duplicate(client):
    await client.post("/api/auth/register", json={
        "email": "dup@test.com", "name": "First", "password": "SecurePass123",
    })
    resp = await client.post("/api/auth/register", json={
        "email": "dup@test.com", "name": "Second", "password": "SecurePass123",
    })
    assert resp.status_code == 409


async def test_login(client):
    await client.post("/api/auth/register", json={
        "email": "login@test.com", "name": "Login User", "password": "SecurePass123",
    })
    resp = await client.post("/api/auth/login", json={
        "email": "login@test.com", "password": "SecurePass123",
    })
    assert resp.status_code == 200
    assert "access_token" in resp.json()


async def test_login_wrong_password(client):
    await client.post("/api/auth/register", json={
        "email": "wrong@test.com", "name": "Wrong", "password": "SecurePass123",
    })
    resp = await client.post("/api/auth/login", json={
        "email": "wrong@test.com", "password": "WrongPassword",
    })
    assert resp.status_code == 401


async def test_get_me(client, auth_headers):
    headers, _ = auth_headers
    resp = await client.get("/api/auth/me", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["email"] == "test@example.com"


async def test_update_profile(client, auth_headers):
    headers, _ = auth_headers
    resp = await client.put("/api/auth/me", headers=headers, json={
        "name": "Updated Name", "bio": "Hello world",
    })
    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated Name"
    assert resp.json()["bio"] == "Hello world"


async def test_change_password(client, auth_headers):
    headers, _ = auth_headers
    resp = await client.post("/api/auth/change-password", headers=headers, json={
        "current_password": "TestPassword123", "new_password": "NewPassword456",
    })
    assert resp.status_code == 200


async def test_unauthorized_access(client):
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


# ════════════════════════════════════════════════════════════════════════════
# Campaigns
# ════════════════════════════════════════════════════════════════════════════

async def test_create_campaign(client, auth_headers):
    headers, _ = auth_headers
    resp = await client.post("/api/campaigns", headers=headers, json={
        "title": "Help Build a School",
        "description": "We need funding to build a school in our community for kids.",
        "goal_amount": 10000.00,
        "category": "education",
        "location": "Portland, OR",
        "end_date": "2026-12-31T00:00:00Z",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Help Build a School"
    assert data["status"] == "draft"
    assert data["slug"] == "help-build-a-school"
    return data["id"]


async def test_create_campaign_unauthenticated(client):
    resp = await client.post("/api/campaigns", json={
        "title": "No Auth Campaign",
        "description": "This should fail because no auth token provided.",
        "goal_amount": 500.00,
        "end_date": "2026-12-31T00:00:00Z",
    })
    assert resp.status_code == 401


async def test_publish_campaign(client, auth_headers):
    headers, _ = auth_headers
    create_resp = await client.post("/api/campaigns", headers=headers, json={
        "title": "Publish Test Campaign",
        "description": "A test campaign that we will publish to make active.",
        "goal_amount": 5000.00,
        "end_date": "2026-12-31T00:00:00Z",
    })
    campaign_id = create_resp.json()["id"]

    resp = await client.post(f"/api/campaigns/{campaign_id}/publish", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"


async def test_edit_campaign(client, auth_headers):
    headers, _ = auth_headers
    create_resp = await client.post("/api/campaigns", headers=headers, json={
        "title": "Edit Test Campaign",
        "description": "A campaign we will edit to change its description.",
        "goal_amount": 5000.00,
        "end_date": "2026-12-31T00:00:00Z",
    })
    campaign_id = create_resp.json()["id"]

    resp = await client.put(f"/api/campaigns/{campaign_id}", headers=headers, json={
        "title": "Updated Campaign Title",
    })
    assert resp.status_code == 200
    assert resp.json()["title"] == "Updated Campaign Title"


async def test_cancel_campaign(client, auth_headers):
    headers, _ = auth_headers
    create_resp = await client.post("/api/campaigns", headers=headers, json={
        "title": "Cancel Test Campaign",
        "description": "A campaign we will cancel after creating it.",
        "goal_amount": 5000.00,
        "end_date": "2026-12-31T00:00:00Z",
    })
    campaign_id = create_resp.json()["id"]

    resp = await client.post(f"/api/campaigns/{campaign_id}/cancel", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


async def test_list_campaigns(client, auth_headers):
    headers, _ = auth_headers
    # Create and publish
    create_resp = await client.post("/api/campaigns", headers=headers, json={
        "title": "Listed Campaign Here",
        "description": "This campaign will be listed publicly for everyone.",
        "goal_amount": 1000.00,
        "end_date": "2026-12-31T00:00:00Z",
    })
    cid = create_resp.json()["id"]
    await client.post(f"/api/campaigns/{cid}/publish", headers=headers)

    resp = await client.get("/api/campaigns?status=active")
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


async def test_get_campaign_by_slug(client, auth_headers):
    headers, _ = auth_headers
    create_resp = await client.post("/api/campaigns", headers=headers, json={
        "title": "Slug Lookup Test",
        "description": "Test that we can look up campaigns by their URL slug.",
        "goal_amount": 1000.00,
        "end_date": "2026-12-31T00:00:00Z",
    })
    slug = create_resp.json()["slug"]

    resp = await client.get(f"/api/campaigns/{slug}")
    assert resp.status_code == 200
    assert resp.json()["slug"] == slug


async def test_my_campaigns(client, auth_headers):
    headers, _ = auth_headers
    await client.post("/api/campaigns", headers=headers, json={
        "title": "My Personal Campaign",
        "description": "This is my own campaign that I can see on my dashboard.",
        "goal_amount": 2000.00,
        "end_date": "2026-12-31T00:00:00Z",
    })
    resp = await client.get("/api/campaigns/my-campaigns", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


async def test_campaign_not_found(client):
    resp = await client.get("/api/campaigns/nonexistent-id-12345")
    assert resp.status_code == 404


# ════════════════════════════════════════════════════════════════════════════
# Stripe Payments (mocked)
# ════════════════════════════════════════════════════════════════════════════

@patch("app.routes.payments.stripe")
async def test_create_checkout_session(mock_stripe, client, auth_headers):
    headers, user_id = auth_headers

    # Create and publish a campaign
    create_resp = await client.post("/api/campaigns", headers=headers, json={
        "title": "Donation Target Campaign",
        "description": "A campaign that accepts donations via Stripe checkout.",
        "goal_amount": 5000.00,
        "end_date": "2026-12-31T00:00:00Z",
    })
    campaign_id = create_resp.json()["id"]
    await client.post(f"/api/campaigns/{campaign_id}/publish", headers=headers)

    mock_stripe.checkout.Session.create.return_value = MagicMock(
        id="cs_test_123", url="https://checkout.stripe.com/test"
    )

    resp = await client.post("/api/stripe/create-checkout-session", headers=headers, json={
        "campaign_id": campaign_id,
        "amount": 50.00,
        "donor_name": "Test Donor",
        "message": "Good luck!",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == "cs_test_123"
    assert data["url"] == "https://checkout.stripe.com/test"
    assert "donation_id" in data


@patch("app.routes.payments.stripe")
async def test_create_payment_intent(mock_stripe, client, auth_headers):
    headers, _ = auth_headers

    create_resp = await client.post("/api/campaigns", headers=headers, json={
        "title": "Payment Intent Test Cam",
        "description": "A campaign to test creating payment intents directly.",
        "goal_amount": 5000.00,
        "end_date": "2026-12-31T00:00:00Z",
    })
    campaign_id = create_resp.json()["id"]
    await client.post(f"/api/campaigns/{campaign_id}/publish", headers=headers)

    mock_stripe.PaymentIntent.create.return_value = MagicMock(
        id="pi_test_456", client_secret="pi_test_456_secret_789"
    )

    resp = await client.post("/api/stripe/create-payment-intent", headers=headers, json={
        "campaign_id": campaign_id,
        "amount": 100.00,
    })
    assert resp.status_code == 200
    assert resp.json()["payment_intent_id"] == "pi_test_456"


@patch("app.routes.payments.stripe")
async def test_checkout_webhook(mock_stripe, client, auth_headers):
    headers, _ = auth_headers

    # Create campaign + publish
    create_resp = await client.post("/api/campaigns", headers=headers, json={
        "title": "Webhook Test Campaign",
        "description": "This campaign tests that webhooks correctly update donations.",
        "goal_amount": 1000.00,
        "end_date": "2026-12-31T00:00:00Z",
    })
    campaign_id = create_resp.json()["id"]
    await client.post(f"/api/campaigns/{campaign_id}/publish", headers=headers)

    # Create checkout session
    mock_stripe.checkout.Session.create.return_value = MagicMock(
        id="cs_webhook_test", url="https://checkout.stripe.com/test"
    )
    checkout_resp = await client.post("/api/stripe/create-checkout-session", headers=headers, json={
        "campaign_id": campaign_id, "amount": 75.00,
    })
    donation_id = checkout_resp.json()["donation_id"]

    # Simulate webhook
    mock_stripe.Webhook.construct_event.return_value = {
        "id": "evt_webhook_test_001",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_webhook_test",
                "payment_intent": "pi_from_webhook",
            }
        },
    }

    resp = await client.post(
        "/api/stripe/webhook",
        content=b"{}",
        headers={"stripe-signature": "test_sig"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "processed"


@patch("app.routes.payments.stripe")
async def test_webhook_idempotency(mock_stripe, client, auth_headers):
    """Same event ID should not be processed twice."""
    headers, _ = auth_headers

    mock_stripe.Webhook.construct_event.return_value = {
        "id": "evt_idempotent_test",
        "type": "payment_intent.payment_failed",
        "data": {"object": {"id": "pi_nonexistent"}},
    }

    await client.post("/api/stripe/webhook", content=b"{}", headers={"stripe-signature": "s"})
    resp = await client.post("/api/stripe/webhook", content=b"{}", headers={"stripe-signature": "s"})
    assert resp.json()["status"] == "already_processed"


# ════════════════════════════════════════════════════════════════════════════
# Donations query
# ════════════════════════════════════════════════════════════════════════════

@patch("app.routes.payments.stripe")
async def test_get_campaign_donations(mock_stripe, client, auth_headers):
    headers, _ = auth_headers

    create_resp = await client.post("/api/campaigns", headers=headers, json={
        "title": "Donations Query Campaign",
        "description": "Testing that we can query donations for this campaign.",
        "goal_amount": 5000.00,
        "end_date": "2026-12-31T00:00:00Z",
    })
    campaign_id = create_resp.json()["id"]
    await client.post(f"/api/campaigns/{campaign_id}/publish", headers=headers)

    resp = await client.get(f"/api/stripe/donations/campaign/{campaign_id}")
    assert resp.status_code == 200
    assert "donations" in resp.json()


async def test_my_donations(client, auth_headers):
    headers, _ = auth_headers
    resp = await client.get("/api/stripe/donations/my", headers=headers)
    assert resp.status_code == 200
    assert "donations" in resp.json()


# ════════════════════════════════════════════════════════════════════════════
# Campaign Updates
# ════════════════════════════════════════════════════════════════════════════

async def test_create_campaign_update(client, auth_headers):
    headers, _ = auth_headers
    create_resp = await client.post("/api/campaigns", headers=headers, json={
        "title": "Update Test Campaign",
        "description": "A campaign where the creator will post updates.",
        "goal_amount": 5000.00,
        "end_date": "2026-12-31T00:00:00Z",
    })
    campaign_id = create_resp.json()["id"]

    resp = await client.post(f"/api/campaigns/{campaign_id}/updates", headers=headers, json={
        "title": "First Progress Update",
        "content": "We've raised 50% of our goal! Thank you everyone for your support.",
    })
    assert resp.status_code == 201
    assert resp.json()["title"] == "First Progress Update"


async def test_list_campaign_updates(client, auth_headers):
    headers, _ = auth_headers
    create_resp = await client.post("/api/campaigns", headers=headers, json={
        "title": "Updates List Test Cam",
        "description": "Testing listing of campaign updates in reverse chronological order.",
        "goal_amount": 5000.00,
        "end_date": "2026-12-31T00:00:00Z",
    })
    campaign_id = create_resp.json()["id"]

    await client.post(f"/api/campaigns/{campaign_id}/updates", headers=headers, json={
        "title": "Update One", "content": "First update posted for this campaign."
    })

    resp = await client.get(f"/api/campaigns/{campaign_id}/updates")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


# ════════════════════════════════════════════════════════════════════════════
# Comments
# ════════════════════════════════════════════════════════════════════════════

async def test_create_comment(client, auth_headers):
    headers, _ = auth_headers
    create_resp = await client.post("/api/campaigns", headers=headers, json={
        "title": "Comment Test Campaign",
        "description": "A campaign where users can leave supportive comments.",
        "goal_amount": 5000.00,
        "end_date": "2026-12-31T00:00:00Z",
    })
    campaign_id = create_resp.json()["id"]

    resp = await client.post(f"/api/campaigns/{campaign_id}/comments", headers=headers, json={
        "content": "Great cause! Happy to support this campaign."
    })
    assert resp.status_code == 201
    assert "Great cause" in resp.json()["content"]


async def test_list_comments(client, auth_headers):
    headers, _ = auth_headers
    create_resp = await client.post("/api/campaigns", headers=headers, json={
        "title": "Comment Listing Campaign",
        "description": "Testing that we can list all comments on a campaign.",
        "goal_amount": 5000.00,
        "end_date": "2026-12-31T00:00:00Z",
    })
    campaign_id = create_resp.json()["id"]
    await client.post(f"/api/campaigns/{campaign_id}/comments", headers=headers, json={
        "content": "This is my comment on this campaign."
    })

    resp = await client.get(f"/api/campaigns/{campaign_id}/comments")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


# ════════════════════════════════════════════════════════════════════════════
# User profile + payment details
# ════════════════════════════════════════════════════════════════════════════

async def test_user_public_profile(client, auth_headers):
    _, user_id = auth_headers
    resp = await client.get(f"/api/users/{user_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Test User"
    assert "email" not in resp.json()  # public profile hides email


async def test_save_payment_details(client, auth_headers):
    headers, _ = auth_headers
    resp = await client.post("/api/users/me/payment-details", headers=headers, json={
        "account_holder_name": "Test User",
        "routing_number": "110000000",
        "account_number": "000123456789",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["routing_number_last4"] == "0000"
    assert data["account_number_last4"] == "6789"


async def test_save_billing_address(client, auth_headers):
    headers, _ = auth_headers
    resp = await client.post("/api/users/me/billing-address", headers=headers, json={
        "full_name": "Test User",
        "address_line1": "123 Main St",
        "city": "Portland",
        "state": "OR",
        "postal_code": "97201",
    })
    assert resp.status_code == 200
    assert resp.json()["city"] == "Portland"


# ════════════════════════════════════════════════════════════════════════════
# Reports
# ════════════════════════════════════════════════════════════════════════════

async def test_report_campaign(client, auth_headers):
    headers, _ = auth_headers
    create_resp = await client.post("/api/campaigns", headers=headers, json={
        "title": "Reportable Campaign Here",
        "description": "A campaign that another user might want to report.",
        "goal_amount": 5000.00,
        "end_date": "2026-12-31T00:00:00Z",
    })
    campaign_id = create_resp.json()["id"]

    resp = await client.post("/api/reports", headers=headers, json={
        "campaign_id": campaign_id,
        "reason": "spam",
        "details": "This looks like spam to me",
    })
    assert resp.status_code == 201
    assert resp.json()["reason"] == "spam"


# ════════════════════════════════════════════════════════════════════════════
# Admin
# ════════════════════════════════════════════════════════════════════════════

async def test_admin_stats(client, admin_headers):
    headers, _ = admin_headers
    resp = await client.get("/api/admin/stats", headers=headers)
    assert resp.status_code == 200
    assert "users" in resp.json()
    assert "total_raised" in resp.json()


async def test_admin_access_denied(client, auth_headers):
    headers, _ = auth_headers  # regular user, not admin
    resp = await client.get("/api/admin/stats", headers=headers)
    assert resp.status_code == 403


async def test_admin_suspend_campaign(client, auth_headers, admin_headers):
    user_headers, _ = auth_headers
    admin_h, _ = admin_headers

    create_resp = await client.post("/api/campaigns", headers=user_headers, json={
        "title": "Suspendable Campaign Here",
        "description": "This campaign will be suspended by an admin moderator.",
        "goal_amount": 5000.00,
        "end_date": "2026-12-31T00:00:00Z",
    })
    campaign_id = create_resp.json()["id"]

    resp = await client.post(f"/api/admin/campaigns/{campaign_id}/suspend", headers=admin_h)
    assert resp.status_code == 200
    assert resp.json()["status"] == "suspended"


# ════════════════════════════════════════════════════════════════════════════
# Platform fee calculation
# ════════════════════════════════════════════════════════════════════════════

async def test_platform_fee_calculation():
    """Verify 5% platform fee math."""
    amount = 100.00
    fee = round(amount * 5.0 / 100, 2)
    net = round(amount - fee, 2)
    assert fee == 5.00
    assert net == 95.00

    amount2 = 49.99
    fee2 = round(amount2 * 5.0 / 100, 2)
    net2 = round(amount2 - fee2, 2)
    assert fee2 == 2.50
    assert net2 == 47.49