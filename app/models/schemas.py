"""
Pydantic schemas — request/response validation for all endpoints
Aligned with actual PostgreSQL database schema.
"""

from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List
from datetime import datetime
from decimal import Decimal


# ════════════════════════════════════════════════════════════════════════════
# Auth
# ════════════════════════════════════════════════════════════════════════════

class RegisterRequest(BaseModel):
    email: EmailStr
    name: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserResponse"


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8, max_length=128)


# ════════════════════════════════════════════════════════════════════════════
# Users (DB table: creators)
# ════════════════════════════════════════════════════════════════════════════

class UserCreate(BaseModel):
    email: EmailStr
    name: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)


class UserUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    last_name: Optional[str] = None
    bio: Optional[str] = None
    website: Optional[str] = None
    username: Optional[str] = None


class UserResponse(BaseModel):
    id: str
    email: Optional[str] = None
    username: Optional[str] = None
    name: Optional[str] = None
    last_name: Optional[str] = None
    user_type: Optional[int] = None
    bio: Optional[str] = None
    phone_number: Optional[str] = None
    address: Optional[str] = None
    state: Optional[str] = None
    time_zone: Optional[str] = None
    website: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class UserPublicResponse(BaseModel):
    """Public-facing user info (no email)"""
    id: str
    name: Optional[str] = None
    last_name: Optional[str] = None
    username: Optional[str] = None
    email: Optional[str] = None
    user_type: Optional[int] = None
    bio: Optional[str] = None
    phone_number: Optional[str] = None
    address: Optional[str] = None
    state: Optional[str] = None
    time_zone: Optional[str] = None
    website: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class UserProfileUpdate(BaseModel):
    """Fields the user can update on their profile."""
    name: Optional[str] = None
    last_name: Optional[str] = None
    username: Optional[str] = Field(None, min_length=3, max_length=30)
    bio: Optional[str] = None
    phone_number: Optional[str] = None
    address: Optional[str] = None
    state: Optional[str] = None
    time_zone: Optional[str] = None
    website: Optional[str] = None
    user_type: Optional[int] = None


# ════════════════════════════════════════════════════════════════════════════
# Campaigns (DB table: campaigns)
# ════════════════════════════════════════════════════════════════════════════

class CampaignCreate(BaseModel):
    title: str = Field(..., min_length=5, max_length=100)
    description: Optional[str] = None  # maps to description_html
    goal_amount: int = Field(..., gt=0)  # funding_goal_cents
    category: Optional[str] = None
    location: Optional[str] = None
    end_date: Optional[str] = None  # ISO format
    bio: Optional[str] = None
    duration_days: Optional[int] = None


class CampaignUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=5, max_length=100)
    description: Optional[str] = None
    category: Optional[str] = None
    location: Optional[str] = None
    end_date: Optional[str] = None
    bio: Optional[str] = None
    duration_days: Optional[int] = None


class CampaignResponse(BaseModel):
    id: int  # campaign_id (bigserial)
    title: str
    slug: Optional[str] = None  # url
    description: Optional[str] = None  # description_html
    goal_amount: float  # funding_goal_cents
    raised_amount: float = 0  # amount_raised_cents
    creator_id: Optional[str] = None
    creator_name: Optional[str] = None  # computed from creators join
    status: Optional[str] = None
    donors_count: int = 0  # backers
    category: Optional[str] = None
    location: Optional[str] = None
    end_date: Optional[datetime] = None
    bio: Optional[str] = None
    duration_days: Optional[int] = None
    funding_percentage: float = 0.0  # computed
    days_left: Optional[int] = None  # computed
    created_at: Optional[datetime] = None  # time_created

    model_config = {"from_attributes": True}


class CampaignListResponse(BaseModel):
    campaigns: List[CampaignResponse]
    total: int
    page: int
    per_page: int


# ════════════════════════════════════════════════════════════════════════════
# Donations (DB table: donations)
# ════════════════════════════════════════════════════════════════════════════

class CheckoutSessionCreate(BaseModel):
    campaign_id: int
    amount: float = Field(..., gt=0)
    donor_name: Optional[str] = "Anonymous"
    donor_email: Optional[EmailStr] = None
    is_anonymous: bool = False
    message: Optional[str] = None
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


class PaymentIntentCreate(BaseModel):
    campaign_id: int
    amount: float = Field(..., gt=0)
    donor_email: Optional[EmailStr] = None
    donor_name: Optional[str] = "Anonymous"
    is_anonymous: bool = False
    message: Optional[str] = None


class DonationResponse(BaseModel):
    id: int  # donation_id
    campaign_id: int
    donor_creator_id: Optional[str] = None
    amount: int = 0
    status: Optional[str] = None
    created_at: Optional[datetime] = None  # time_created
    # Stripe fields (not in DB yet, kept for future use)
    campaign_title: Optional[str] = None
    donor_name: Optional[str] = None
    donor_email: Optional[str] = None
    is_anonymous: bool = False
    message: Optional[str] = None
    platform_fee: Optional[float] = None
    processing_fee: Optional[float] = None
    net_amount: Optional[float] = None
    currency: Optional[str] = None
    stripe_payment_intent_id: Optional[str] = None
    refund_amount: Optional[float] = None

    model_config = {"from_attributes": True}


class DonationListResponse(BaseModel):
    donations: List[DonationResponse]
    total: int


# ════════════════════════════════════════════════════════════════════════════
# Refunds (DB table: refunds)
# ════════════════════════════════════════════════════════════════════════════

class RefundRequestCreate(BaseModel):
    donation_id: int
    reason: Optional[str] = None
    amount: Optional[int] = None  # null = full refund


class RefundRequestResponse(BaseModel):
    id: int  # refund_id
    donation_id: int
    payment_id: Optional[int] = None
    amount: Optional[int] = None
    status: Optional[str] = None
    time_initiated: Optional[datetime] = None
    time_paid: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ════════════════════════════════════════════════════════════════════════════
# Payouts (DB table: payouts)
# ════════════════════════════════════════════════════════════════════════════

class PayoutResponse(BaseModel):
    id: int  # payout_id
    campaign_id: int
    payee_creator_id: Optional[str] = None
    amount: int = 0
    time_initiated: Optional[datetime] = None
    time_paid: Optional[datetime] = None
    # Stripe fields (not in DB yet, kept for future use)
    currency: Optional[str] = None
    status: Optional[str] = None
    stripe_transfer_id: Optional[str] = None
    stripe_payout_id: Optional[str] = None

    model_config = {"from_attributes": True}


# ════════════════════════════════════════════════════════════════════════════
# Campaign Updates (not in DB diagram — kept for future use)
# ════════════════════════════════════════════════════════════════════════════

class CampaignUpdateCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    content: str = Field(..., min_length=10)


class CampaignUpdateResponse(BaseModel):
    id: Optional[str] = None
    campaign_id: Optional[str] = None
    author_id: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ════════════════════════════════════════════════════════════════════════════
# Comments (DB table: comments)
# ════════════════════════════════════════════════════════════════════════════

class CommentCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)


class CommentResponse(BaseModel):
    id: int  # comment_id
    campaign_id: int
    creator_id: Optional[str] = None
    comment_text: Optional[str] = None
    created_at: Optional[datetime] = None  # time_created
    # Computed fields (not in DB)
    user_name: Optional[str] = None
    user_avatar: Optional[str] = None

    model_config = {"from_attributes": True}


# ════════════════════════════════════════════════════════════════════════════
# Reports (DB table: reports)
# ════════════════════════════════════════════════════════════════════════════

class ReportCreate(BaseModel):
    campaign_id: int
    strength_id: Optional[int] = None


class ReportResponse(BaseModel):
    id: int  # report_id
    reporter_id: str
    campaign_id: Optional[int] = None
    strength_id: Optional[int] = None
    created_at: Optional[datetime] = None  # time_created

    model_config = {"from_attributes": True}


# ════════════════════════════════════════════════════════════════════════════
# Payments (DB table: payments)
# ════════════════════════════════════════════════════════════════════════════

class PaymentResponse(BaseModel):
    id: int  # payment_id
    donation_id: int
    processor: Optional[str] = None
    status: Optional[str] = None
    time_captured: Optional[datetime] = None
    time_settled: Optional[datetime] = None
    time_created: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ════════════════════════════════════════════════════════════════════════════
# Fees (DB table: fees)
# ════════════════════════════════════════════════════════════════════════════

class FeeResponse(BaseModel):
    id: int  # fee_id
    campaign_id: int
    donation_id: int
    amount: int = 0
    time_created: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ════════════════════════════════════════════════════════════════════════════
# Bank Details (DB table: bank_details)
# ════════════════════════════════════════════════════════════════════════════

class BankDetailsCreate(BaseModel):
    campaign_id: int
    routing_number: str
    account_number: str
    account_type: str


class BankDetailsResponse(BaseModel):
    campaign_id: int
    routing_number: Optional[str] = None
    account_number: Optional[str] = None
    account_type: Optional[str] = None

    model_config = {"from_attributes": True}


# ════════════════════════════════════════════════════════════════════════════
# Collaborators (DB table: collaborators)
# ════════════════════════════════════════════════════════════════════════════

class CollaboratorCreate(BaseModel):
    campaign_id: int
    email: str


class CollaboratorResponse(BaseModel):
    id: int  # collaborator_id
    campaign_id: int
    email: Optional[str] = None
    status: Optional[str] = None
    time_created: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ════════════════════════════════════════════════════════════════════════════
# FAQs (DB table: faqs)
# ════════════════════════════════════════════════════════════════════════════

class FaqCreate(BaseModel):
    campaign_id: int
    display_order: int = 0
    question: str
    answer: str


class FaqResponse(BaseModel):
    campaign_id: int
    display_order: int = 0
    question: Optional[str] = None
    answer: Optional[str] = None

    model_config = {"from_attributes": True}


# ════════════════════════════════════════════════════════════════════════════
# Rewards (DB table: rewards)
# ════════════════════════════════════════════════════════════════════════════

class RewardCreate(BaseModel):
    campaign_id: int
    title: str
    required_amount_cents: int = 0
    description: Optional[str] = None
    limit_total: Optional[int] = None
    display_order: int = 0


class RewardResponse(BaseModel):
    id: int  # reward_id
    campaign_id: int
    title: Optional[str] = None
    required_amount_cents: int = 0
    description: Optional[str] = None
    limit_total: Optional[int] = None
    display_order: int = 0

    model_config = {"from_attributes": True}


# ════════════════════════════════════════════════════════════════════════════
# Saved Campaigns (DB table: saved_campaigns)
# ════════════════════════════════════════════════════════════════════════════

class SavedCampaignCreate(BaseModel):
    creator_id: str
    campaign_id: int
    engagement_type: int = 0


class SavedCampaignResponse(BaseModel):
    creator_id: str
    campaign_id: int
    engagement_type: int = 0
    time_created: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ════════════════════════════════════════════════════════════════════════════
# Campaign Photos (DB table: campaign_photos)
# ════════════════════════════════════════════════════════════════════════════

class CampaignPhotoResponse(BaseModel):
    id: int  # photo_id
    campaign_id: int
    s3_bucket: Optional[str] = None
    s3_key: Optional[str] = None
    content_type: Optional[str] = None
    file_size_bytes: Optional[int] = None
    width_px: Optional[int] = None
    height_px: Optional[int] = None
    is_primary: bool = False
    sort_order: int = 0
    uploaded_by_creator_id: Optional[str] = None
    time_created: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ════════════════════════════════════════════════════════════════════════════
# Interests (DB table: interests)
# ════════════════════════════════════════════════════════════════════════════

class InterestResponse(BaseModel):
    id: int  # interest_id
    name: Optional[str] = None
    time_created: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ════════════════════════════════════════════════════════════════════════════
# Campaign Types (DB table: campaign_types)
# ════════════════════════════════════════════════════════════════════════════

class CampaignTypeResponse(BaseModel):
    id: int  # type_id
    name: Optional[str] = None
    description: Optional[str] = None
    time_created: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ════════════════════════════════════════════════════════════════════════════
# Payment Details (kept for Stripe integration)
# ════════════════════════════════════════════════════════════════════════════

class PaymentDetailCreate(BaseModel):
    account_type: str = "individual"
    account_holder_name: str = Field(..., min_length=1)
    routing_number: str = Field(..., min_length=9, max_length=9)
    account_number: str = Field(..., min_length=4, max_length=17)


class PaymentDetailResponse(BaseModel):
    id: Optional[str] = None
    user_id: Optional[str] = None
    account_type: Optional[str] = None
    account_holder_name: Optional[str] = None
    routing_number_last4: Optional[str] = None
    account_number_last4: Optional[str] = None
    is_verified: bool = False
    is_default: bool = True
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ════════════════════════════════════════════════════════════════════════════
# Billing Address (kept for Stripe integration)
# ════════════════════════════════════════════════════════════════════════════

class BillingAddressCreate(BaseModel):
    full_name: str
    address_line1: str
    address_line2: Optional[str] = None
    city: str
    state: Optional[str] = None
    postal_code: str
    country: str = "US"


class BillingAddressResponse(BaseModel):
    id: Optional[str] = None
    user_id: Optional[str] = None
    full_name: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    country: Optional[str] = None
    is_default: bool = True
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ════════════════════════════════════════════════════════════════════════════
# Stripe Setup Intent
# ════════════════════════════════════════════════════════════════════════════

class SetupIntentResponse(BaseModel):
    client_secret: str
    setup_intent_id: str


class PaymentMethodResponse(BaseModel):
    id: str
    brand: str
    last4: str
    exp_month: int
    exp_year: int


# ════════════════════════════════════════════════════════════════════════════
# Search
# ════════════════════════════════════════════════════════════════════════════

class SearchResponse(BaseModel):
    campaigns: List[CampaignResponse]
    total: int
    query: str
    filters: dict


# Forward ref resolution
TokenResponse.model_rebuild()