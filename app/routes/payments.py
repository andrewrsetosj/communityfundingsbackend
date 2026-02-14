"""
Stripe Payment routes — the full money flow
  1. Donors pay via Checkout Sessions or Payment Intents
  2. Webhooks confirm payments and update database
  3. Creators onboard via Stripe Connect
  4. Platform transfers funds to creators
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from decimal import Decimal
from typing import Optional, List
from datetime import datetime, timezone
import stripe
import json
import os

from app.database import get_db
from app.auth import get_current_user, get_optional_user
from app.models.models import (
    Campaign, Donation, User, WebhookEvent, Payout,
    DonationStatus, CampaignStatus, PayoutStatus,
)
from app.models.schemas import (
    CheckoutSessionCreate, PaymentIntentCreate,
    DonationResponse, DonationListResponse,
    SetupIntentResponse, PaymentMethodResponse, PayoutResponse,
)

router = APIRouter(prefix="/api/stripe", tags=["payments"])

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
PLATFORM_FEE_PERCENT = float(os.getenv("PLATFORM_FEE_PERCENT", "5.0"))


# ════════════════════════════════════════════════════════════════════════════
# 1. DONOR CHECKOUT — Create Stripe sessions for donations
# ════════════════════════════════════════════════════════════════════════════

@router.post("/create-checkout-session")
async def create_checkout_session(
    data: CheckoutSessionCreate,
    user: Optional[User] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a Stripe Checkout session for a donation."""
    result = await db.execute(select(Campaign).where(Campaign.id == data.campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.status != CampaignStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Campaign is not accepting donations")

    amount_cents = int(data.amount * 100)
    platform_fee = round(data.amount * PLATFORM_FEE_PERCENT / 100, 2)
    net_amount = round(data.amount - platform_fee, 2)

    # Create pending donation record
    donation = Donation(
        campaign_id=data.campaign_id,
        donor_id=user.id if user else None,
        donor_name=data.donor_name or (user.name if user else "Anonymous"),
        donor_email=data.donor_email or (user.email if user else None),
        is_anonymous=data.is_anonymous,
        message=data.message,
        amount=Decimal(str(data.amount)),
        platform_fee=Decimal(str(platform_fee)),
        net_amount=Decimal(str(net_amount)),
        status=DonationStatus.PENDING,
    )
    db.add(donation)
    await db.flush()

    success_url = data.success_url or f"{FRONTEND_URL}/campaigns/{campaign.slug}/thank-you?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = data.cancel_url or f"{FRONTEND_URL}/campaigns/{campaign.slug}"

    # Build Stripe session params
    session_params = {
        "payment_method_types": ["card"],
        "line_items": [{
            "price_data": {
                "currency": "usd",
                "unit_amount": amount_cents,
                "product_data": {
                    "name": f"Donation to {campaign.title}",
                    "description": f"Supporting: {campaign.title}",
                },
            },
            "quantity": 1,
        }],
        "mode": "payment",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "metadata": {
            "donation_id": donation.id,
            "campaign_id": campaign.id,
            "donor_name": donation.donor_name,
            "platform": "community_fundings",
        },
    }

    # If donor has email, pre-fill and send receipt
    if donation.donor_email:
        session_params["customer_email"] = donation.donor_email

    # If campaign creator has Stripe Connect, use destination charges
    creator_result = await db.execute(select(User).where(User.id == campaign.creator_id))
    creator = creator_result.scalar_one_or_none()
    if creator and creator.stripe_connect_account_id and creator.stripe_connect_onboarded:
        session_params["payment_intent_data"] = {
            "application_fee_amount": int(platform_fee * 100),
            "transfer_data": {
                "destination": creator.stripe_connect_account_id,
            },
        }

    checkout_session = stripe.checkout.Session.create(**session_params)

    donation.stripe_checkout_session_id = checkout_session.id
    await db.flush()

    return {
        "session_id": checkout_session.id,
        "url": checkout_session.url,
        "donation_id": donation.id,
    }


@router.post("/create-payment-intent")
async def create_payment_intent(
    data: PaymentIntentCreate,
    user: Optional[User] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a Stripe Payment Intent (for custom payment forms)."""
    result = await db.execute(select(Campaign).where(Campaign.id == data.campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.status != CampaignStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Campaign is not accepting donations")

    amount_cents = int(data.amount * 100)
    platform_fee = round(data.amount * PLATFORM_FEE_PERCENT / 100, 2)
    net_amount = round(data.amount - platform_fee, 2)

    donation = Donation(
        campaign_id=data.campaign_id,
        donor_id=user.id if user else None,
        donor_name=data.donor_name or (user.name if user else "Anonymous"),
        donor_email=data.donor_email or (user.email if user else None),
        is_anonymous=data.is_anonymous,
        message=data.message,
        amount=Decimal(str(data.amount)),
        platform_fee=Decimal(str(platform_fee)),
        net_amount=Decimal(str(net_amount)),
        status=DonationStatus.PENDING,
    )
    db.add(donation)
    await db.flush()

    # Build PI params
    pi_params = {
        "amount": amount_cents,
        "currency": "usd",
        "metadata": {
            "donation_id": donation.id,
            "campaign_id": campaign.id,
            "platform": "community_fundings",
        },
    }

    # Stripe Connect destination charges
    creator_result = await db.execute(select(User).where(User.id == campaign.creator_id))
    creator = creator_result.scalar_one_or_none()
    if creator and creator.stripe_connect_account_id and creator.stripe_connect_onboarded:
        pi_params["application_fee_amount"] = int(platform_fee * 100)
        pi_params["transfer_data"] = {"destination": creator.stripe_connect_account_id}

    intent = stripe.PaymentIntent.create(**pi_params)

    donation.stripe_payment_intent_id = intent.id
    await db.flush()

    return {
        "client_secret": intent.client_secret,
        "payment_intent_id": intent.id,
        "donation_id": donation.id,
    }


# ════════════════════════════════════════════════════════════════════════════
# 2. WEBHOOK — Stripe sends events here
# ════════════════════════════════════════════════════════════════════════════

@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Process Stripe webhook events with signature verification and idempotency."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Idempotency check
    existing = await db.execute(
        select(WebhookEvent).where(WebhookEvent.stripe_event_id == event["id"])
    )
    if existing.scalar_one_or_none():
        return {"status": "already_processed"}

    webhook_event = WebhookEvent(
        stripe_event_id=event["id"],
        event_type=event["type"],
        payload=json.dumps(event["data"]),
        processed=False,
    )
    db.add(webhook_event)

    event_type = event["type"]
    data_object = event["data"]["object"]

    # ── checkout.session.completed ──
    if event_type == "checkout.session.completed":
        session_id = data_object.get("id")
        donation_result = await db.execute(
            select(Donation).where(Donation.stripe_checkout_session_id == session_id)
        )
        donation = donation_result.scalar_one_or_none()
        if donation and donation.status == DonationStatus.PENDING:
            donation.status = DonationStatus.SUCCEEDED
            donation.stripe_payment_intent_id = data_object.get("payment_intent")

            campaign_result = await db.execute(
                select(Campaign).where(Campaign.id == donation.campaign_id)
            )
            campaign = campaign_result.scalar_one_or_none()
            if campaign:
                campaign.raised_amount = (campaign.raised_amount or 0) + donation.amount
                campaign.donors_count = (campaign.donors_count or 0) + 1
                if float(campaign.raised_amount) >= float(campaign.goal_amount):
                    campaign.status = CampaignStatus.FUNDED

    # ── payment_intent.succeeded ──
    elif event_type == "payment_intent.succeeded":
        pi_id = data_object.get("id")
        donation_result = await db.execute(
            select(Donation).where(Donation.stripe_payment_intent_id == pi_id)
        )
        donation = donation_result.scalar_one_or_none()
        if donation and donation.status == DonationStatus.PENDING:
            donation.status = DonationStatus.SUCCEEDED
            donation.stripe_charge_id = (
                data_object.get("latest_charge")
                or (data_object.get("charges", {}).get("data", [{}])[0].get("id") if data_object.get("charges") else None)
            )

            campaign_result = await db.execute(
                select(Campaign).where(Campaign.id == donation.campaign_id)
            )
            campaign = campaign_result.scalar_one_or_none()
            if campaign:
                campaign.raised_amount = (campaign.raised_amount or 0) + donation.amount
                campaign.donors_count = (campaign.donors_count or 0) + 1
                if float(campaign.raised_amount) >= float(campaign.goal_amount):
                    campaign.status = CampaignStatus.FUNDED

    # ── payment_intent.payment_failed ──
    elif event_type == "payment_intent.payment_failed":
        pi_id = data_object.get("id")
        donation_result = await db.execute(
            select(Donation).where(Donation.stripe_payment_intent_id == pi_id)
        )
        donation = donation_result.scalar_one_or_none()
        if donation:
            donation.status = DonationStatus.FAILED

    # ── charge.refunded ──
    elif event_type == "charge.refunded":
        charge_id = data_object.get("id")
        pi_id = data_object.get("payment_intent")
        donation_result = await db.execute(
            select(Donation).where(
                (Donation.stripe_charge_id == charge_id) | (Donation.stripe_payment_intent_id == pi_id)
            )
        )
        donation = donation_result.scalar_one_or_none()
        if donation:
            refund_amount = data_object.get("amount_refunded", 0) / 100
            if refund_amount >= float(donation.amount):
                donation.status = DonationStatus.REFUNDED
            else:
                donation.status = DonationStatus.PARTIALLY_REFUNDED
            donation.refund_amount = Decimal(str(refund_amount))

            campaign_result = await db.execute(
                select(Campaign).where(Campaign.id == donation.campaign_id)
            )
            campaign = campaign_result.scalar_one_or_none()
            if campaign:
                campaign.raised_amount = max(
                    Decimal("0"), (campaign.raised_amount or 0) - Decimal(str(refund_amount))
                )

    # ── account.updated (Stripe Connect) ──
    elif event_type == "account.updated":
        account_id = data_object.get("id")
        charges_enabled = data_object.get("charges_enabled", False)
        if charges_enabled:
            user_result = await db.execute(
                select(User).where(User.stripe_connect_account_id == account_id)
            )
            user = user_result.scalar_one_or_none()
            if user:
                user.stripe_connect_onboarded = True

    webhook_event.processed = True
    await db.flush()
    return {"status": "processed", "event_type": event_type}


# ════════════════════════════════════════════════════════════════════════════
# 3. STRIPE CONNECT — Onboard creators to receive payouts
# ════════════════════════════════════════════════════════════════════════════

@router.post("/connect/onboard")
async def create_connect_account(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a Stripe Connect Express account for the campaign creator.
    Returns an onboarding link they must complete.
    """
    if user.stripe_connect_account_id:
        # Already has account — generate new link to finish onboarding
        account_link = stripe.AccountLink.create(
            account=user.stripe_connect_account_id,
            refresh_url=f"{FRONTEND_URL}/dashboard/payments?refresh=true",
            return_url=f"{FRONTEND_URL}/dashboard/payments?onboarded=true",
            type="account_onboarding",
        )
        return {"url": account_link.url, "account_id": user.stripe_connect_account_id}

    account = stripe.Account.create(
        type="express",
        country="US",
        email=user.email,
        capabilities={"card_payments": {"requested": True}, "transfers": {"requested": True}},
        metadata={"user_id": user.id, "platform": "community_fundings"},
    )

    user.stripe_connect_account_id = account.id
    await db.flush()

    account_link = stripe.AccountLink.create(
        account=account.id,
        refresh_url=f"{FRONTEND_URL}/dashboard/payments?refresh=true",
        return_url=f"{FRONTEND_URL}/dashboard/payments?onboarded=true",
        type="account_onboarding",
    )

    return {"url": account_link.url, "account_id": account.id}


@router.get("/connect/status")
async def connect_status(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Check if the creator's Stripe Connect account is fully onboarded."""
    if not user.stripe_connect_account_id:
        return {"onboarded": False, "account_id": None}

    try:
        account = stripe.Account.retrieve(user.stripe_connect_account_id)
        onboarded = account.get("charges_enabled", False)

        if onboarded and not user.stripe_connect_onboarded:
            user.stripe_connect_onboarded = True
            await db.flush()

        return {
            "onboarded": onboarded,
            "account_id": user.stripe_connect_account_id,
            "charges_enabled": account.get("charges_enabled", False),
            "payouts_enabled": account.get("payouts_enabled", False),
        }
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/connect/dashboard-link")
async def connect_dashboard_link(user: User = Depends(get_current_user)):
    """Generate a link to the creator's Stripe Express dashboard."""
    if not user.stripe_connect_account_id:
        raise HTTPException(status_code=400, detail="No Stripe Connect account")
    login_link = stripe.Account.create_login_link(user.stripe_connect_account_id)
    return {"url": login_link.url}


# ════════════════════════════════════════════════════════════════════════════
# 4. PAYOUTS — Transfer campaign funds to creator
# ════════════════════════════════════════════════════════════════════════════

@router.post("/payouts/{campaign_id}", response_model=PayoutResponse)
async def request_payout(
    campaign_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Creator requests payout of raised funds for a campaign."""
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.creator_id != user.id:
        raise HTTPException(status_code=403, detail="Not your campaign")
    if not user.stripe_connect_account_id or not user.stripe_connect_onboarded:
        raise HTTPException(status_code=400, detail="Complete Stripe Connect onboarding first")

    # Calculate available amount (succeeded donations minus already-paid-out)
    donations_result = await db.execute(
        select(Donation)
        .where(Donation.campaign_id == campaign_id, Donation.status == DonationStatus.SUCCEEDED)
    )
    total_net = sum(float(d.net_amount or 0) for d in donations_result.scalars().all())

    existing_payouts = await db.execute(
        select(Payout).where(
            Payout.campaign_id == campaign_id,
            Payout.status.in_([PayoutStatus.PENDING, PayoutStatus.PROCESSING, PayoutStatus.PAID]),
        )
    )
    already_paid = sum(float(p.amount) for p in existing_payouts.scalars().all())

    available = round(total_net - already_paid, 2)
    if available <= 0:
        raise HTTPException(status_code=400, detail="No funds available for payout")

    # Create transfer via Stripe Connect
    try:
        transfer = stripe.Transfer.create(
            amount=int(available * 100),
            currency="usd",
            destination=user.stripe_connect_account_id,
            metadata={
                "campaign_id": campaign_id,
                "user_id": user.id,
                "platform": "community_fundings",
            },
        )
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=f"Stripe error: {str(e)}")

    payout = Payout(
        campaign_id=campaign_id,
        creator_id=user.id,
        amount=Decimal(str(available)),
        status=PayoutStatus.PAID,
        stripe_transfer_id=transfer.id,
        completed_at=datetime.now(timezone.utc),
    )
    db.add(payout)
    await db.flush()

    return PayoutResponse(
        id=payout.id, campaign_id=payout.campaign_id,
        amount=float(payout.amount), currency=payout.currency,
        status=payout.status.value, stripe_transfer_id=payout.stripe_transfer_id,
        created_at=payout.created_at, completed_at=payout.completed_at,
    )


@router.get("/payouts/{campaign_id}", response_model=List[PayoutResponse])
async def get_payouts(
    campaign_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get payout history for a campaign."""
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.creator_id != user.id:
        raise HTTPException(status_code=403, detail="Not your campaign")

    payouts_result = await db.execute(
        select(Payout).where(Payout.campaign_id == campaign_id).order_by(Payout.created_at.desc())
    )
    return [
        PayoutResponse(
            id=p.id, campaign_id=p.campaign_id,
            amount=float(p.amount), currency=p.currency,
            status=p.status.value, stripe_transfer_id=p.stripe_transfer_id,
            created_at=p.created_at, completed_at=p.completed_at,
        )
        for p in payouts_result.scalars().all()
    ]


# ════════════════════════════════════════════════════════════════════════════
# 5. DONATIONS — Query donation records
# ════════════════════════════════════════════════════════════════════════════

@router.get("/donations/campaign/{campaign_id}", response_model=DonationListResponse)
async def get_campaign_donations(
    campaign_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get all donations for a campaign (public — hides anonymous emails)."""
    result = await db.execute(
        select(Donation)
        .where(Donation.campaign_id == campaign_id, Donation.status == DonationStatus.SUCCEEDED)
        .order_by(Donation.created_at.desc())
    )
    donations = result.scalars().all()
    return DonationListResponse(
        donations=[
            DonationResponse(
                id=d.id, campaign_id=d.campaign_id,
                donor_name="Anonymous" if d.is_anonymous else d.donor_name,
                donor_email=None,  # never expose donor emails publicly
                is_anonymous=d.is_anonymous,
                message=d.message,
                amount=float(d.amount),
                currency=d.currency,
                status=d.status.value if isinstance(d.status, DonationStatus) else d.status,
                created_at=d.created_at,
            )
            for d in donations
        ],
        total=len(donations),
    )


@router.get("/donations/my", response_model=DonationListResponse)
async def get_my_donations(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all donations made by the authenticated user."""
    result = await db.execute(
        select(Donation).where(Donation.donor_id == user.id).order_by(Donation.created_at.desc())
    )
    donations = result.scalars().all()
    return DonationListResponse(
        donations=[
            DonationResponse(
                id=d.id, campaign_id=d.campaign_id,
                campaign_title=d.campaign.title if d.campaign else None,
                donor_name=d.donor_name, donor_email=d.donor_email,
                is_anonymous=d.is_anonymous,
                message=d.message,
                amount=float(d.amount),
                platform_fee=float(d.platform_fee) if d.platform_fee else None,
                net_amount=float(d.net_amount) if d.net_amount else None,
                currency=d.currency,
                status=d.status.value if isinstance(d.status, DonationStatus) else d.status,
                stripe_payment_intent_id=d.stripe_payment_intent_id,
                refund_amount=float(d.refund_amount) if d.refund_amount else None,
                created_at=d.created_at,
            )
            for d in donations
        ],
        total=len(donations),
    )


# ════════════════════════════════════════════════════════════════════════════
# 6. SAVED PAYMENT METHODS — For returning donors
# ════════════════════════════════════════════════════════════════════════════

@router.post("/setup-intent", response_model=SetupIntentResponse)
async def create_setup_intent(user: User = Depends(get_current_user)):
    """Create a Stripe Setup Intent to save a payment method for future donations."""
    if not user.stripe_customer_id:
        customer = stripe.Customer.create(email=user.email, name=user.name)
        # Note: caller must save customer_id to user record
        customer_id = customer.id
    else:
        customer_id = user.stripe_customer_id

    intent = stripe.SetupIntent.create(
        customer=customer_id,
        payment_method_types=["card"],
    )
    return SetupIntentResponse(client_secret=intent.client_secret, setup_intent_id=intent.id)


@router.get("/payment-methods", response_model=List[PaymentMethodResponse])
async def get_payment_methods(user: User = Depends(get_current_user)):
    """List saved cards for the authenticated user."""
    if not user.stripe_customer_id:
        return []
    methods = stripe.PaymentMethod.list(customer=user.stripe_customer_id, type="card")
    return [
        PaymentMethodResponse(
            id=pm.id,
            brand=pm.card.brand,
            last4=pm.card.last4,
            exp_month=pm.card.exp_month,
            exp_year=pm.card.exp_year,
        )
        for pm in methods.data
    ]


@router.delete("/payment-methods/{pm_id}")
async def detach_payment_method(pm_id: str, user: User = Depends(get_current_user)):
    """Remove a saved payment method."""
    try:
        pm = stripe.PaymentMethod.retrieve(pm_id)
        if pm.customer != user.stripe_customer_id:
            raise HTTPException(status_code=403, detail="Not your payment method")
        stripe.PaymentMethod.detach(pm_id)
        return {"deleted": True}
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))