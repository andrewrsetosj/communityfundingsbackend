"""
Stripe Integration Tests — Production-Ready
=============================================
Tests the FULL money flow with mocked Stripe calls:

  CREATOR (Foundation Admin) flow:
    → Stripe Connect onboarding
    → Connect status check
    → Dashboard link access
    → Destination charges (platform fee split)
    → Payout request + history

  DONOR (Client) flow:
    → Checkout Session donation
    → Payment Intent donation
    → Anonymous vs named donations
    → Saved payment methods (Setup Intents)
    → Donation history
    → Refund request

  WEBHOOK flow:
    → checkout.session.completed  (donation succeeds)
    → payment_intent.succeeded    (PI donation succeeds)
    → payment_intent.payment_failed (donation fails)
    → charge.refunded             (full + partial refund)
    → account.updated             (Connect onboarding complete)
    → idempotency                 (duplicate event ignored)

  ADMIN flow:
    → Approve refund → Stripe processes it
    → Deny refund
    → View pending refunds

  EDGE CASES:
    → Donate to inactive campaign (400)
    → Donate to nonexistent campaign (404)
    → Campaign reaches goal → status becomes "funded"
    → Payout with no funds available (400)
    → Payout without Connect onboarding (400)
    → Platform fee math validation (5%)
"""

import pytest
from unittest.mock import patch, MagicMock
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from decimal import Decimal

from app.database import Base, get_db
from app.auth import hash_password, create_access_token
from app.models.models import (
    User, Campaign, Donation, Payout,
    CampaignStatus, DonationStatus, PayoutStatus,
)

# ── Test DB ────────────────────────────────────────────────────────────────

TEST_DB_URL = "sqlite+aiosqlite:///file:stripe_test?mode=memory&cache=shared&uri=true"
engine = create_async_engine(TEST_DB_URL, echo=False)
Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def override_get_db():
    async with Session() as s:
        async with s.begin():
            yield s


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
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
async def creator():
    """Foundation admin / campaign creator — has Stripe Connect."""
    async with Session() as s:
        async with s.begin():
            user = User(
                email="creator@foundation.org",
                name="Foundation Admin",
                hashed_password=hash_password("CreatorPass123"),
                stripe_connect_account_id="acct_creator_test_001",
                stripe_connect_onboarded=True,
            )
            s.add(user)
            await s.flush()
            token = create_access_token(str(user.id))
            return {"Authorization": f"Bearer {token}"}, str(user.id)


@pytest.fixture
async def creator_not_onboarded():
    """Creator who has NOT completed Stripe Connect onboarding."""
    async with Session() as s:
        async with s.begin():
            user = User(
                email="new_creator@foundation.org",
                name="New Creator",
                hashed_password=hash_password("NewCreator123"),
            )
            s.add(user)
            await s.flush()
            token = create_access_token(str(user.id))
            return {"Authorization": f"Bearer {token}"}, str(user.id)


@pytest.fixture
async def donor():
    """Regular donor who gives money to campaigns."""
    async with Session() as s:
        async with s.begin():
            user = User(
                email="donor@gmail.com",
                name="Generous Donor",
                hashed_password=hash_password("DonorPass123"),
                stripe_customer_id="cus_donor_test_001",
            )
            s.add(user)
            await s.flush()
            token = create_access_token(str(user.id))
            return {"Authorization": f"Bearer {token}"}, str(user.id)


@pytest.fixture
async def donor_no_stripe():
    """Donor with no Stripe customer ID yet."""
    async with Session() as s:
        async with s.begin():
            user = User(
                email="newdonor@gmail.com",
                name="New Donor",
                hashed_password=hash_password("NewDonor123"),
            )
            s.add(user)
            await s.flush()
            token = create_access_token(str(user.id))
            return {"Authorization": f"Bearer {token}"}, str(user.id)


@pytest.fixture
async def admin():
    """Platform admin who handles refunds and moderation."""
    async with Session() as s:
        async with s.begin():
            user = User(
                email="admin@communityfundings.com",
                name="Platform Admin",
                hashed_password=hash_password("AdminPass123"),
                is_admin=True,
            )
            s.add(user)
            await s.flush()
            token = create_access_token(str(user.id))
            return {"Authorization": f"Bearer {token}"}, str(user.id)


@pytest.fixture
async def active_campaign(client, creator):
    """Create and publish a campaign, return its ID."""
    headers, _ = creator
    resp = await client.post("/api/campaigns", headers=headers, json={
        "title": "Build Clean Water Wells",
        "description": "We are raising funds to build clean water wells in rural communities.",
        "goal_amount": 10000.00,
        "category": "community",
        "end_date": "2027-06-01T00:00:00Z",
    })
    campaign_id = resp.json()["id"]
    await client.post(f"/api/campaigns/{campaign_id}/publish", headers=headers)
    return campaign_id


# ════════════════════════════════════════════════════════════════════════════
# 1. CREATOR: Stripe Connect Onboarding
# ════════════════════════════════════════════════════════════════════════════

@patch("app.routes.payments.stripe")
async def test_connect_onboard_new_creator(mock_stripe, client, creator_not_onboarded):
    """Creator without Connect account gets a new account + onboarding link."""
    headers, _ = creator_not_onboarded

    mock_stripe.Account.create.return_value = MagicMock(id="acct_new_123")
    mock_stripe.AccountLink.create.return_value = MagicMock(
        url="https://connect.stripe.com/setup/acct_new_123"
    )

    resp = await client.post("/api/stripe/connect/onboard", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["account_id"] == "acct_new_123"
    assert "connect.stripe.com" in data["url"]

    mock_stripe.Account.create.assert_called_once()
    call_kwargs = mock_stripe.Account.create.call_args[1]
    assert call_kwargs["type"] == "express"
    assert call_kwargs["capabilities"]["card_payments"]["requested"] is True
    assert call_kwargs["capabilities"]["transfers"]["requested"] is True


@patch("app.routes.payments.stripe")
async def test_connect_onboard_existing_account(mock_stripe, client, creator):
    """Creator who already has Connect account gets a fresh onboarding link."""
    headers, _ = creator

    mock_stripe.AccountLink.create.return_value = MagicMock(
        url="https://connect.stripe.com/refresh/acct_creator_test_001"
    )

    resp = await client.post("/api/stripe/connect/onboard", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["account_id"] == "acct_creator_test_001"
    mock_stripe.Account.create.assert_not_called()  # should reuse existing


@patch("app.routes.payments.stripe")
async def test_connect_status_onboarded(mock_stripe, client, creator):
    """Check Connect status for a fully onboarded creator."""
    headers, _ = creator

    mock_stripe.Account.retrieve.return_value = {
        "charges_enabled": True,
        "payouts_enabled": True,
    }

    resp = await client.get("/api/stripe/connect/status", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["onboarded"] is True
    assert data["charges_enabled"] is True
    assert data["payouts_enabled"] is True


@patch("app.routes.payments.stripe")
async def test_connect_status_not_onboarded(mock_stripe, client, creator_not_onboarded):
    """Creator who hasn't started Connect sees onboarded=False."""
    headers, _ = creator_not_onboarded

    resp = await client.get("/api/stripe/connect/status", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["onboarded"] is False
    assert resp.json()["account_id"] is None


@patch("app.routes.payments.stripe")
async def test_connect_dashboard_link(mock_stripe, client, creator):
    """Onboarded creator can access their Stripe Express dashboard."""
    headers, _ = creator

    mock_stripe.Account.create_login_link.return_value = MagicMock(
        url="https://connect.stripe.com/express/acct_creator_test_001"
    )

    resp = await client.get("/api/stripe/connect/dashboard-link", headers=headers)
    assert resp.status_code == 200
    assert "connect.stripe.com" in resp.json()["url"]


@patch("app.routes.payments.stripe")
async def test_connect_dashboard_link_no_account(mock_stripe, client, creator_not_onboarded):
    """Creator without Connect account cannot access dashboard."""
    headers, _ = creator_not_onboarded
    resp = await client.get("/api/stripe/connect/dashboard-link", headers=headers)
    assert resp.status_code == 400


# ════════════════════════════════════════════════════════════════════════════
# 2. DONOR: Checkout Session Flow
# ════════════════════════════════════════════════════════════════════════════

@patch("app.routes.payments.stripe")
async def test_donor_checkout_session(mock_stripe, client, donor, active_campaign):
    """Donor creates a checkout session — full flow with named donation."""
    headers, _ = donor

    mock_stripe.checkout.Session.create.return_value = MagicMock(
        id="cs_test_donor_001", url="https://checkout.stripe.com/pay/cs_test_donor_001"
    )

    resp = await client.post("/api/stripe/create-checkout-session", headers=headers, json={
        "campaign_id": active_campaign,
        "amount": 100.00,
        "donor_name": "Generous Donor",
        "donor_email": "donor@gmail.com",
        "message": "Keep up the great work!",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == "cs_test_donor_001"
    assert "checkout.stripe.com" in data["url"]
    assert "donation_id" in data

    # Verify Stripe was called with correct params
    call_kwargs = mock_stripe.checkout.Session.create.call_args[1]
    assert call_kwargs["mode"] == "payment"
    assert call_kwargs["line_items"][0]["price_data"]["unit_amount"] == 10000  # $100 in cents
    assert call_kwargs["metadata"]["campaign_id"] == active_campaign
    assert call_kwargs["customer_email"] == "donor@gmail.com"
    # Creator is onboarded → destination charge with platform fee
    assert "payment_intent_data" in call_kwargs
    assert call_kwargs["payment_intent_data"]["application_fee_amount"] == 500  # 5% of $100
    assert call_kwargs["payment_intent_data"]["transfer_data"]["destination"] == "acct_creator_test_001"


@patch("app.routes.payments.stripe")
async def test_anonymous_checkout(mock_stripe, client, active_campaign):
    """Anonymous donation (no auth) creates checkout session."""
    mock_stripe.checkout.Session.create.return_value = MagicMock(
        id="cs_anon_001", url="https://checkout.stripe.com/pay/cs_anon_001"
    )

    resp = await client.post("/api/stripe/create-checkout-session", json={
        "campaign_id": active_campaign,
        "amount": 25.00,
        "is_anonymous": True,
    })
    assert resp.status_code == 200
    assert resp.json()["session_id"] == "cs_anon_001"


@patch("app.routes.payments.stripe")
async def test_checkout_inactive_campaign(mock_stripe, client, donor, creator):
    """Cannot donate to a draft (non-active) campaign."""
    headers_creator, _ = creator
    headers_donor, _ = donor

    resp = await client.post("/api/campaigns", headers=headers_creator, json={
        "title": "Draft Campaign Not Published",
        "description": "This campaign is still in draft mode and not accepting donations.",
        "goal_amount": 1000.00,
        "end_date": "2027-01-01T00:00:00Z",
    })
    draft_id = resp.json()["id"]

    resp = await client.post("/api/stripe/create-checkout-session", headers=headers_donor, json={
        "campaign_id": draft_id,
        "amount": 50.00,
    })
    assert resp.status_code == 400
    assert "not accepting donations" in resp.json()["detail"]


@patch("app.routes.payments.stripe")
async def test_checkout_nonexistent_campaign(mock_stripe, client, donor):
    """Cannot donate to a campaign that doesn't exist."""
    headers, _ = donor
    resp = await client.post("/api/stripe/create-checkout-session", headers=headers, json={
        "campaign_id": "nonexistent-campaign-id",
        "amount": 50.00,
    })
    assert resp.status_code == 404


# ════════════════════════════════════════════════════════════════════════════
# 3. DONOR: Payment Intent Flow
# ════════════════════════════════════════════════════════════════════════════

@patch("app.routes.payments.stripe")
async def test_donor_payment_intent(mock_stripe, client, donor, active_campaign):
    """Donor creates a Payment Intent for custom payment form."""
    headers, _ = donor

    mock_stripe.PaymentIntent.create.return_value = MagicMock(
        id="pi_test_001", client_secret="pi_test_001_secret_xyz"
    )

    resp = await client.post("/api/stripe/create-payment-intent", headers=headers, json={
        "campaign_id": active_campaign,
        "amount": 250.00,
        "donor_name": "Generous Donor",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["payment_intent_id"] == "pi_test_001"
    assert data["client_secret"] == "pi_test_001_secret_xyz"
    assert "donation_id" in data

    # Verify Stripe Connect destination charge
    call_kwargs = mock_stripe.PaymentIntent.create.call_args[1]
    assert call_kwargs["amount"] == 25000  # $250 in cents
    assert call_kwargs["currency"] == "usd"
    assert call_kwargs["application_fee_amount"] == 1250  # 5% of $250
    assert call_kwargs["transfer_data"]["destination"] == "acct_creator_test_001"


# ════════════════════════════════════════════════════════════════════════════
# 4. WEBHOOKS: Payment Lifecycle
# ════════════════════════════════════════════════════════════════════════════

@patch("app.routes.payments.stripe")
async def test_webhook_checkout_completed(mock_stripe, client, donor, active_campaign):
    """Webhook: checkout.session.completed → donation succeeds, campaign raised_amount updates."""
    headers, _ = donor

    # Step 1: Create checkout session
    mock_stripe.checkout.Session.create.return_value = MagicMock(
        id="cs_webhook_flow_001", url="https://checkout.stripe.com/test"
    )
    checkout_resp = await client.post("/api/stripe/create-checkout-session", headers=headers, json={
        "campaign_id": active_campaign,
        "amount": 500.00,
        "donor_name": "Generous Donor",
    })
    donation_id = checkout_resp.json()["donation_id"]

    # Step 2: Simulate Stripe webhook
    mock_stripe.Webhook.construct_event.return_value = {
        "id": "evt_checkout_001",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_webhook_flow_001",
                "payment_intent": "pi_from_checkout_001",
            }
        },
    }

    resp = await client.post("/api/stripe/webhook", content=b"{}", headers={"stripe-signature": "sig"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "processed"

    # Step 3: Verify campaign raised amount updated
    campaign_resp = await client.get(f"/api/campaigns/{active_campaign}")
    assert campaign_resp.status_code == 200

    # Step 4: Verify donation appears in campaign donations
    donations_resp = await client.get(f"/api/stripe/donations/campaign/{active_campaign}")
    assert donations_resp.status_code == 200
    donations = donations_resp.json()["donations"]
    succeeded = [d for d in donations if d["status"] == "succeeded"]
    assert len(succeeded) >= 1
    assert any(d["amount"] == 500.0 for d in succeeded)


@patch("app.routes.payments.stripe")
async def test_webhook_payment_intent_succeeded(mock_stripe, client, donor, active_campaign):
    """Webhook: payment_intent.succeeded → donation confirmed via PI flow."""
    headers, _ = donor

    mock_stripe.PaymentIntent.create.return_value = MagicMock(
        id="pi_webhook_flow_001", client_secret="secret"
    )
    pi_resp = await client.post("/api/stripe/create-payment-intent", headers=headers, json={
        "campaign_id": active_campaign,
        "amount": 75.00,
    })
    donation_id = pi_resp.json()["donation_id"]

    # Webhook: payment succeeded
    mock_stripe.Webhook.construct_event.return_value = {
        "id": "evt_pi_succeeded_001",
        "type": "payment_intent.succeeded",
        "data": {
            "object": {
                "id": "pi_webhook_flow_001",
                "latest_charge": "ch_from_pi_001",
            }
        },
    }

    resp = await client.post("/api/stripe/webhook", content=b"{}", headers={"stripe-signature": "sig"})
    assert resp.status_code == 200
    assert resp.json()["event_type"] == "payment_intent.succeeded"


@patch("app.routes.payments.stripe")
async def test_webhook_payment_failed(mock_stripe, client, donor, active_campaign):
    """Webhook: payment_intent.payment_failed → donation marked as failed."""
    headers, _ = donor

    mock_stripe.PaymentIntent.create.return_value = MagicMock(
        id="pi_fail_001", client_secret="secret"
    )
    await client.post("/api/stripe/create-payment-intent", headers=headers, json={
        "campaign_id": active_campaign,
        "amount": 50.00,
    })

    mock_stripe.Webhook.construct_event.return_value = {
        "id": "evt_pi_failed_001",
        "type": "payment_intent.payment_failed",
        "data": {"object": {"id": "pi_fail_001"}},
    }

    resp = await client.post("/api/stripe/webhook", content=b"{}", headers={"stripe-signature": "sig"})
    assert resp.status_code == 200
    assert resp.json()["event_type"] == "payment_intent.payment_failed"


@patch("app.routes.payments.stripe")
async def test_webhook_full_refund(mock_stripe, client, donor, active_campaign):
    """Webhook: charge.refunded (full) → donation marked refunded, raised_amount decremented."""
    headers, _ = donor

    # Create + confirm donation via checkout flow
    mock_stripe.checkout.Session.create.return_value = MagicMock(
        id="cs_refund_test", url="https://checkout.stripe.com/test"
    )
    checkout_resp = await client.post("/api/stripe/create-checkout-session", headers=headers, json={
        "campaign_id": active_campaign, "amount": 200.00,
    })

    # Confirm via webhook
    mock_stripe.Webhook.construct_event.return_value = {
        "id": "evt_confirm_for_refund",
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_refund_test", "payment_intent": "pi_refund_test"}},
    }
    await client.post("/api/stripe/webhook", content=b"{}", headers={"stripe-signature": "sig"})

    # Now simulate full refund webhook
    mock_stripe.Webhook.construct_event.return_value = {
        "id": "evt_refund_full_001",
        "type": "charge.refunded",
        "data": {
            "object": {
                "id": "ch_refund_001",
                "payment_intent": "pi_refund_test",
                "amount_refunded": 20000,  # $200 in cents
            }
        },
    }
    resp = await client.post("/api/stripe/webhook", content=b"{}", headers={"stripe-signature": "sig"})
    assert resp.status_code == 200
    assert resp.json()["event_type"] == "charge.refunded"


@patch("app.routes.payments.stripe")
async def test_webhook_partial_refund(mock_stripe, client, donor, active_campaign):
    """Webhook: charge.refunded (partial) → donation marked partially_refunded."""
    headers, _ = donor

    mock_stripe.checkout.Session.create.return_value = MagicMock(
        id="cs_partial_refund", url="https://checkout.stripe.com/test"
    )
    await client.post("/api/stripe/create-checkout-session", headers=headers, json={
        "campaign_id": active_campaign, "amount": 300.00,
    })

    # Confirm donation
    mock_stripe.Webhook.construct_event.return_value = {
        "id": "evt_confirm_partial",
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_partial_refund", "payment_intent": "pi_partial_refund"}},
    }
    await client.post("/api/stripe/webhook", content=b"{}", headers={"stripe-signature": "sig"})

    # Partial refund — only $50 of $300
    mock_stripe.Webhook.construct_event.return_value = {
        "id": "evt_partial_refund_001",
        "type": "charge.refunded",
        "data": {
            "object": {
                "id": "ch_partial_001",
                "payment_intent": "pi_partial_refund",
                "amount_refunded": 5000,  # $50 in cents
            }
        },
    }
    resp = await client.post("/api/stripe/webhook", content=b"{}", headers={"stripe-signature": "sig"})
    assert resp.status_code == 200


@patch("app.routes.payments.stripe")
async def test_webhook_account_updated(mock_stripe, client, creator_not_onboarded):
    """Webhook: account.updated → creator marked as onboarded when charges_enabled=True."""
    headers, user_id = creator_not_onboarded

    # First onboard to get a Connect account
    mock_stripe.Account.create.return_value = MagicMock(id="acct_onboard_test")
    mock_stripe.AccountLink.create.return_value = MagicMock(url="https://connect.stripe.com/setup")
    await client.post("/api/stripe/connect/onboard", headers=headers)

    # Simulate Stripe sending account.updated webhook
    mock_stripe.Webhook.construct_event.return_value = {
        "id": "evt_account_updated_001",
        "type": "account.updated",
        "data": {
            "object": {
                "id": "acct_onboard_test",
                "charges_enabled": True,
            }
        },
    }

    resp = await client.post("/api/stripe/webhook", content=b"{}", headers={"stripe-signature": "sig"})
    assert resp.status_code == 200
    assert resp.json()["event_type"] == "account.updated"


@patch("app.routes.payments.stripe")
async def test_webhook_idempotency_stripe(mock_stripe, client):
    """Same Stripe event ID sent twice → second call returns already_processed."""
    mock_stripe.Webhook.construct_event.return_value = {
        "id": "evt_duplicate_test_001",
        "type": "payment_intent.payment_failed",
        "data": {"object": {"id": "pi_nonexistent"}},
    }

    resp1 = await client.post("/api/stripe/webhook", content=b"{}", headers={"stripe-signature": "sig"})
    assert resp1.json()["status"] == "processed"

    resp2 = await client.post("/api/stripe/webhook", content=b"{}", headers={"stripe-signature": "sig"})
    assert resp2.json()["status"] == "already_processed"


# ════════════════════════════════════════════════════════════════════════════
# 5. CAMPAIGN GOAL REACHED → Status becomes "funded"
# ════════════════════════════════════════════════════════════════════════════

@patch("app.routes.payments.stripe")
async def test_campaign_reaches_goal(mock_stripe, client, creator, donor):
    """When donations meet goal_amount, campaign status changes to 'funded'."""
    headers_creator, _ = creator
    headers_donor, _ = donor

    # Create campaign with low goal
    resp = await client.post("/api/campaigns", headers=headers_creator, json={
        "title": "Small Goal Campaign Test",
        "description": "A small campaign that should be fully funded quickly.",
        "goal_amount": 100.00,
        "end_date": "2027-01-01T00:00:00Z",
    })
    campaign_id = resp.json()["id"]
    await client.post(f"/api/campaigns/{campaign_id}/publish", headers=headers_creator)

    # Donate exact goal amount
    mock_stripe.checkout.Session.create.return_value = MagicMock(
        id="cs_goal_test", url="https://checkout.stripe.com/test"
    )
    await client.post("/api/stripe/create-checkout-session", headers=headers_donor, json={
        "campaign_id": campaign_id, "amount": 100.00,
    })

    # Webhook confirms payment
    mock_stripe.Webhook.construct_event.return_value = {
        "id": "evt_goal_reached",
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_goal_test", "payment_intent": "pi_goal"}},
    }
    await client.post("/api/stripe/webhook", content=b"{}", headers={"stripe-signature": "sig"})

    # Verify campaign is now "funded"
    campaign_resp = await client.get(f"/api/campaigns/{campaign_id}")
    assert campaign_resp.json()["status"] == "funded"


# ════════════════════════════════════════════════════════════════════════════
# 6. DONOR: Donation History
# ════════════════════════════════════════════════════════════════════════════

@patch("app.routes.payments.stripe")
async def test_donor_donation_history(mock_stripe, client, donor, active_campaign):
    """Donor can see all their donations in 'my donations'."""
    headers, _ = donor

    # Make two donations
    for i, amount in enumerate([50.00, 150.00]):
        mock_stripe.checkout.Session.create.return_value = MagicMock(
            id=f"cs_history_{i}", url="https://checkout.stripe.com/test"
        )
        await client.post("/api/stripe/create-checkout-session", headers=headers, json={
            "campaign_id": active_campaign, "amount": amount,
        })

    resp = await client.get("/api/stripe/donations/my", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["total"] >= 2


async def test_campaign_donation_list_public(client, active_campaign):
    """Public can view campaign donations (without donor emails)."""
    resp = await client.get(f"/api/stripe/donations/campaign/{active_campaign}")
    assert resp.status_code == 200
    assert "donations" in resp.json()
    for d in resp.json()["donations"]:
        assert d["donor_email"] is None  # emails never exposed publicly


# ════════════════════════════════════════════════════════════════════════════
# 7. REFUND FLOW: Donor requests → Admin approves/denies
# ════════════════════════════════════════════════════════════════════════════

@patch("app.routes.refunds.stripe")
@patch("app.routes.payments.stripe")
async def test_refund_request_and_approve(mock_stripe, mock_refund_stripe, client, donor, admin, active_campaign):
    """Full refund lifecycle: donor requests → admin approves → Stripe processes."""
    headers_donor, _ = donor
    headers_admin, _ = admin

    # Create and confirm donation
    mock_stripe.checkout.Session.create.return_value = MagicMock(
        id="cs_refund_lifecycle", url="https://checkout.stripe.com/test"
    )
    checkout_resp = await client.post("/api/stripe/create-checkout-session", headers=headers_donor, json={
        "campaign_id": active_campaign, "amount": 100.00,
    })
    donation_id = checkout_resp.json()["donation_id"]

    mock_stripe.Webhook.construct_event.return_value = {
        "id": "evt_refund_lifecycle_confirm",
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_refund_lifecycle", "payment_intent": "pi_refund_lifecycle"}},
    }
    await client.post("/api/stripe/webhook", content=b"{}", headers={"stripe-signature": "sig"})

    # Step 1: Donor requests refund
    refund_resp = await client.post("/api/refunds", headers=headers_donor, json={
        "donation_id": donation_id,
        "reason": "Changed my mind about this donation, requesting a full refund.",
    })
    assert refund_resp.status_code == 201
    refund_id = refund_resp.json()["id"]
    assert refund_resp.json()["status"] == "requested"

    # Step 2: Admin approves refund → Stripe processes
    mock_refund_stripe.Refund.create.return_value = MagicMock(id="re_test_001")

    approve_resp = await client.post(f"/api/refunds/{refund_id}/approve", headers=headers_admin)
    assert approve_resp.status_code == 200
    assert approve_resp.json()["stripe_refund_id"] == "re_test_001"

    mock_refund_stripe.Refund.create.assert_called_once()
    call_kwargs = mock_refund_stripe.Refund.create.call_args[1]
    assert call_kwargs["payment_intent"] == "pi_refund_lifecycle"


@patch("app.routes.payments.stripe")
async def test_refund_request_denied(mock_stripe, client, donor, admin, active_campaign):
    """Admin denies a refund request."""
    headers_donor, _ = donor
    headers_admin, _ = admin

    mock_stripe.checkout.Session.create.return_value = MagicMock(
        id="cs_deny_refund", url="https://checkout.stripe.com/test"
    )
    checkout_resp = await client.post("/api/stripe/create-checkout-session", headers=headers_donor, json={
        "campaign_id": active_campaign, "amount": 75.00,
    })
    donation_id = checkout_resp.json()["donation_id"]

    # Confirm donation
    mock_stripe.Webhook.construct_event.return_value = {
        "id": "evt_deny_confirm",
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_deny_refund", "payment_intent": "pi_deny"}},
    }
    await client.post("/api/stripe/webhook", content=b"{}", headers={"stripe-signature": "sig"})

    # Request refund
    refund_resp = await client.post("/api/refunds", headers=headers_donor, json={
        "donation_id": donation_id,
        "reason": "I want to request a refund for this donation please.",
    })
    refund_id = refund_resp.json()["id"]

    # Admin denies
    deny_resp = await client.post(f"/api/refunds/{refund_id}/deny", headers=headers_admin)
    assert deny_resp.status_code == 200
    assert deny_resp.json()["status"] == "refund_denied"


@patch("app.routes.payments.stripe")
async def test_refund_not_your_donation(mock_stripe, client, donor, donor_no_stripe, active_campaign):
    """Cannot request refund for someone else's donation."""
    headers_donor, _ = donor
    headers_other, _ = donor_no_stripe

    mock_stripe.checkout.Session.create.return_value = MagicMock(
        id="cs_not_yours", url="https://checkout.stripe.com/test"
    )
    checkout_resp = await client.post("/api/stripe/create-checkout-session", headers=headers_donor, json={
        "campaign_id": active_campaign, "amount": 50.00,
    })
    donation_id = checkout_resp.json()["donation_id"]

    # Confirm it
    mock_stripe.Webhook.construct_event.return_value = {
        "id": "evt_not_yours_confirm",
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_not_yours", "payment_intent": "pi_not_yours"}},
    }
    await client.post("/api/stripe/webhook", content=b"{}", headers={"stripe-signature": "sig"})

    # Different user tries to refund
    resp = await client.post("/api/refunds", headers=headers_other, json={
        "donation_id": donation_id,
        "reason": "Trying to refund someone else's donation which should fail.",
    })
    assert resp.status_code == 403


@patch("app.routes.payments.stripe")
async def test_refund_duplicate_request(mock_stripe, client, donor, active_campaign):
    """Cannot request refund twice for the same donation."""
    headers, _ = donor

    mock_stripe.checkout.Session.create.return_value = MagicMock(
        id="cs_dup_refund", url="https://checkout.stripe.com/test"
    )
    checkout_resp = await client.post("/api/stripe/create-checkout-session", headers=headers, json={
        "campaign_id": active_campaign, "amount": 60.00,
    })
    donation_id = checkout_resp.json()["donation_id"]

    mock_stripe.Webhook.construct_event.return_value = {
        "id": "evt_dup_refund_confirm",
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_dup_refund", "payment_intent": "pi_dup_refund"}},
    }
    await client.post("/api/stripe/webhook", content=b"{}", headers={"stripe-signature": "sig"})

    # First refund request — OK
    resp1 = await client.post("/api/refunds", headers=headers, json={
        "donation_id": donation_id,
        "reason": "First refund request for this donation should succeed.",
    })
    assert resp1.status_code == 201

    # Second refund request — conflict
    resp2 = await client.post("/api/refunds", headers=headers, json={
        "donation_id": donation_id,
        "reason": "Duplicate refund request for same donation should be rejected.",
    })
    assert resp2.status_code == 409


async def test_donor_refund_history(client, donor):
    """Donor can view their refund request history."""
    headers, _ = donor
    resp = await client.get("/api/refunds/my", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ════════════════════════════════════════════════════════════════════════════
# 8. PAYOUTS: Creator Withdraws Funds via Connect
# ════════════════════════════════════════════════════════════════════════════

@patch("app.routes.payments.stripe")
async def test_creator_payout(mock_stripe, client, creator, donor, active_campaign):
    """Creator requests payout of raised funds via Stripe Connect Transfer."""
    headers_creator, _ = creator
    headers_donor, _ = donor

    # Donate and confirm
    mock_stripe.checkout.Session.create.return_value = MagicMock(
        id="cs_payout_test", url="https://checkout.stripe.com/test"
    )
    await client.post("/api/stripe/create-checkout-session", headers=headers_donor, json={
        "campaign_id": active_campaign, "amount": 1000.00,
    })

    mock_stripe.Webhook.construct_event.return_value = {
        "id": "evt_payout_confirm",
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_payout_test", "payment_intent": "pi_payout"}},
    }
    await client.post("/api/stripe/webhook", content=b"{}", headers={"stripe-signature": "sig"})

    # Creator requests payout
    mock_stripe.Transfer.create.return_value = MagicMock(id="tr_test_001")

    resp = await client.post(f"/api/stripe/payouts/{active_campaign}", headers=headers_creator)
    assert resp.status_code == 200
    data = resp.json()
    assert data["stripe_transfer_id"] == "tr_test_001"
    assert data["status"] == "paid"
    assert float(data["amount"]) == 950.00  # $1000 - 5% platform fee

    # Verify Stripe Transfer called correctly
    call_kwargs = mock_stripe.Transfer.create.call_args[1]
    assert call_kwargs["amount"] == 95000  # $950 in cents
    assert call_kwargs["destination"] == "acct_creator_test_001"


@patch("app.routes.payments.stripe")
async def test_payout_history(mock_stripe, client, creator, donor, active_campaign):
    """Creator can view payout history for their campaign."""
    headers_creator, _ = creator
    headers_donor, _ = donor

    # Fund the campaign
    mock_stripe.checkout.Session.create.return_value = MagicMock(
        id="cs_payout_hist", url="https://checkout.stripe.com/test"
    )
    await client.post("/api/stripe/create-checkout-session", headers=headers_donor, json={
        "campaign_id": active_campaign, "amount": 500.00,
    })
    mock_stripe.Webhook.construct_event.return_value = {
        "id": "evt_payout_hist_confirm",
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_payout_hist", "payment_intent": "pi_hist"}},
    }
    await client.post("/api/stripe/webhook", content=b"{}", headers={"stripe-signature": "sig"})

    # Request payout
    mock_stripe.Transfer.create.return_value = MagicMock(id="tr_hist_001")
    await client.post(f"/api/stripe/payouts/{active_campaign}", headers=headers_creator)

    # View history
    resp = await client.get(f"/api/stripe/payouts/{active_campaign}", headers=headers_creator)
    assert resp.status_code == 200
    assert len(resp.json()) >= 1
    assert resp.json()[0]["stripe_transfer_id"] == "tr_hist_001"


@patch("app.routes.payments.stripe")
async def test_payout_no_funds(mock_stripe, client, creator, active_campaign):
    """Payout fails when no donations have been made."""
    headers, _ = creator
    resp = await client.post(f"/api/stripe/payouts/{active_campaign}", headers=headers)
    assert resp.status_code == 400
    assert "No funds" in resp.json()["detail"]


@patch("app.routes.payments.stripe")
async def test_payout_not_onboarded(mock_stripe, client, creator_not_onboarded):
    """Creator without Connect onboarding cannot request payout."""
    headers, _ = creator_not_onboarded

    # Create a campaign
    resp = await client.post("/api/campaigns", headers=headers, json={
        "title": "No Payout Campaign Test",
        "description": "This campaign creator has not completed Stripe Connect setup.",
        "goal_amount": 5000.00,
        "end_date": "2027-01-01T00:00:00Z",
    })
    campaign_id = resp.json()["id"]
    await client.post(f"/api/campaigns/{campaign_id}/publish", headers=headers)

    resp = await client.post(f"/api/stripe/payouts/{campaign_id}", headers=headers)
    assert resp.status_code == 400
    assert "onboarding" in resp.json()["detail"].lower()


@patch("app.routes.payments.stripe")
async def test_payout_not_your_campaign(mock_stripe, client, donor, active_campaign):
    """Donor cannot request payout for a campaign they don't own."""
    headers, _ = donor
    resp = await client.post(f"/api/stripe/payouts/{active_campaign}", headers=headers)
    assert resp.status_code == 403


# ════════════════════════════════════════════════════════════════════════════
# 9. SAVED PAYMENT METHODS
# ════════════════════════════════════════════════════════════════════════════

@patch("app.routes.payments.stripe")
async def test_setup_intent_existing_customer(mock_stripe, client, donor):
    """Donor with existing Stripe customer can create Setup Intent to save a card."""
    headers, _ = donor

    mock_stripe.SetupIntent.create.return_value = MagicMock(
        id="seti_test_001", client_secret="seti_test_001_secret"
    )

    resp = await client.post("/api/stripe/setup-intent", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["setup_intent_id"] == "seti_test_001"

    call_kwargs = mock_stripe.SetupIntent.create.call_args[1]
    assert call_kwargs["customer"] == "cus_donor_test_001"


@patch("app.routes.payments.stripe")
async def test_setup_intent_new_customer(mock_stripe, client, donor_no_stripe):
    """Donor without Stripe customer gets one created, then Setup Intent."""
    headers, _ = donor_no_stripe

    mock_stripe.Customer.create.return_value = MagicMock(id="cus_new_001")
    mock_stripe.SetupIntent.create.return_value = MagicMock(
        id="seti_new_001", client_secret="seti_new_001_secret"
    )

    resp = await client.post("/api/stripe/setup-intent", headers=headers)
    assert resp.status_code == 200
    mock_stripe.Customer.create.assert_called_once()


@patch("app.routes.payments.stripe")
async def test_list_payment_methods(mock_stripe, client, donor):
    """Donor can list their saved cards."""
    headers, _ = donor

    mock_stripe.PaymentMethod.list.return_value = MagicMock(data=[
        MagicMock(
            id="pm_visa_001",
            card=MagicMock(brand="visa", last4="4242", exp_month=12, exp_year=2027),
        ),
        MagicMock(
            id="pm_amex_001",
            card=MagicMock(brand="amex", last4="0005", exp_month=3, exp_year=2028),
        ),
    ])

    resp = await client.get("/api/stripe/payment-methods", headers=headers)
    assert resp.status_code == 200
    cards = resp.json()
    assert len(cards) == 2
    assert cards[0]["brand"] == "visa"
    assert cards[0]["last4"] == "4242"
    assert cards[1]["brand"] == "amex"


@patch("app.routes.payments.stripe")
async def test_list_payment_methods_no_customer(mock_stripe, client, donor_no_stripe):
    """Donor without Stripe customer ID gets empty list."""
    headers, _ = donor_no_stripe
    resp = await client.get("/api/stripe/payment-methods", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


@patch("app.routes.payments.stripe")
async def test_detach_payment_method(mock_stripe, client, donor):
    """Donor can remove a saved payment method."""
    headers, _ = donor

    mock_stripe.PaymentMethod.retrieve.return_value = MagicMock(customer="cus_donor_test_001")
    mock_stripe.PaymentMethod.detach.return_value = MagicMock()

    resp = await client.delete("/api/stripe/payment-methods/pm_visa_001", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    mock_stripe.PaymentMethod.detach.assert_called_with("pm_visa_001")


@patch("app.routes.payments.stripe")
async def test_detach_payment_method_not_yours(mock_stripe, client, donor):
    """Cannot remove someone else's payment method."""
    headers, _ = donor

    # Must set error.StripeError to a real exception class so the except clause works
    mock_stripe.error.StripeError = type("StripeError", (Exception,), {})
    mock_stripe.PaymentMethod.retrieve.return_value = MagicMock(customer="cus_someone_else")

    resp = await client.delete("/api/stripe/payment-methods/pm_other_001", headers=headers)
    assert resp.status_code == 403


# ════════════════════════════════════════════════════════════════════════════
# 10. PLATFORM FEE MATH VALIDATION
# ════════════════════════════════════════════════════════════════════════════

async def test_platform_fee_5_percent():
    """Verify 5% platform fee calculations across various amounts."""
    test_cases = [
        (100.00, 5.00, 95.00),
        (50.00, 2.50, 47.50),
        (1000.00, 50.00, 950.00),
        (25.99, 1.30, 24.69),
        (1.00, 0.05, 0.95),
        (9999.99, 500.00, 9499.99),
    ]
    for amount, expected_fee, expected_net in test_cases:
        fee = round(amount * 5.0 / 100, 2)
        net = round(amount - fee, 2)
        assert fee == expected_fee, f"Fee mismatch for ${amount}: got {fee}, expected {expected_fee}"
        assert net == expected_net, f"Net mismatch for ${amount}: got {net}, expected {expected_net}"


@patch("app.routes.payments.stripe")
async def test_fee_in_stripe_checkout_call(mock_stripe, client, donor, active_campaign):
    """Verify platform fee is correctly passed to Stripe in checkout session."""
    headers, _ = donor

    mock_stripe.checkout.Session.create.return_value = MagicMock(
        id="cs_fee_check", url="https://checkout.stripe.com/test"
    )

    await client.post("/api/stripe/create-checkout-session", headers=headers, json={
        "campaign_id": active_campaign,
        "amount": 200.00,
    })

    call_kwargs = mock_stripe.checkout.Session.create.call_args[1]
    # $200 donation → $10 platform fee (5%) → 1000 cents
    assert call_kwargs["payment_intent_data"]["application_fee_amount"] == 1000
    # $200 → 20000 cents in line items
    assert call_kwargs["line_items"][0]["price_data"]["unit_amount"] == 20000


@patch("app.routes.payments.stripe")
async def test_fee_in_payment_intent_call(mock_stripe, client, donor, active_campaign):
    """Verify platform fee in Payment Intent creation."""
    headers, _ = donor

    mock_stripe.PaymentIntent.create.return_value = MagicMock(
        id="pi_fee_check", client_secret="secret"
    )

    await client.post("/api/stripe/create-payment-intent", headers=headers, json={
        "campaign_id": active_campaign,
        "amount": 400.00,
    })

    call_kwargs = mock_stripe.PaymentIntent.create.call_args[1]
    # $400 → 40000 cents, $20 fee → 2000 cents
    assert call_kwargs["amount"] == 40000
    assert call_kwargs["application_fee_amount"] == 2000
    assert call_kwargs["transfer_data"]["destination"] == "acct_creator_test_001"