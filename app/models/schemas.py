"""
Pydantic schemas — request/response validation for all endpoints
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
# Users
# ════════════════════════════════════════════════════════════════════════════

class UserCreate(BaseModel):
    email: EmailStr
    name: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)


class UserUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    bio: Optional[str] = None
    avatar_url: Optional[str] = None


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    avatar_url: Optional[str] = None
    bio: Optional[str] = None
    email_verified: bool
    stripe_connect_onboarded: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class UserPublicResponse(BaseModel):
    """Public-facing user info (no email)"""
    id: str
    name: str
    avatar_url: Optional[str] = None
    bio: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ════════════════════════════════════════════════════════════════════════════
# Campaigns
# ════════════════════════════════════════════════════════════════════════════

class CampaignCreate(BaseModel):
    title: str = Field(..., min_length=5, max_length=200)
    short_description: Optional[str] = Field(None, max_length=300)
    description: str = Field(..., min_length=20)
    goal_amount: float = Field(..., gt=0)
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    category: Optional[str] = None
    location: Optional[str] = None
    end_date: str  # ISO format


class CampaignUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=5, max_length=200)
    short_description: Optional[str] = Field(None, max_length=300)
    description: Optional[str] = Field(None, min_length=20)
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    category: Optional[str] = None
    location: Optional[str] = None
    end_date: Optional[str] = None


class CampaignResponse(BaseModel):
    id: str
    title: str
    slug: str
    short_description: Optional[str] = None
    description: str
    goal_amount: float
    raised_amount: float
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    creator_id: str
    creator_name: Optional[str] = None
    status: str
    donors_count: int
    category: Optional[str] = None
    location: Optional[str] = None
    end_date: Optional[datetime] = None
    is_featured: bool = False
    funding_percentage: float
    days_left: Optional[int] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class CampaignListResponse(BaseModel):
    campaigns: List[CampaignResponse]
    total: int
    page: int
    per_page: int


# ════════════════════════════════════════════════════════════════════════════
# Donations
# ════════════════════════════════════════════════════════════════════════════

class CheckoutSessionCreate(BaseModel):
    campaign_id: str
    amount: float = Field(..., gt=0)
    donor_name: Optional[str] = "Anonymous"
    donor_email: Optional[EmailStr] = None
    is_anonymous: bool = False
    message: Optional[str] = None
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


class PaymentIntentCreate(BaseModel):
    campaign_id: str
    amount: float = Field(..., gt=0)
    donor_email: Optional[EmailStr] = None
    donor_name: Optional[str] = "Anonymous"
    is_anonymous: bool = False
    message: Optional[str] = None


class DonationResponse(BaseModel):
    id: str
    campaign_id: str
    campaign_title: Optional[str] = None
    donor_name: Optional[str] = None
    donor_email: Optional[str] = None
    is_anonymous: bool = False
    message: Optional[str] = None
    amount: float
    platform_fee: Optional[float] = None
    processing_fee: Optional[float] = None
    net_amount: Optional[float] = None
    currency: str
    status: str
    stripe_payment_intent_id: Optional[str] = None
    refund_amount: Optional[float] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class DonationListResponse(BaseModel):
    donations: List[DonationResponse]
    total: int


# ════════════════════════════════════════════════════════════════════════════
# Refunds
# ════════════════════════════════════════════════════════════════════════════

class RefundRequestCreate(BaseModel):
    donation_id: str
    reason: str = Field(..., min_length=10)
    amount: Optional[float] = None  # null = full refund


class RefundRequestResponse(BaseModel):
    id: str
    donation_id: str
    reason: str
    amount: Optional[float] = None
    status: str
    admin_notes: Optional[str] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ════════════════════════════════════════════════════════════════════════════
# Payouts (Stripe Connect transfers to creators)
# ════════════════════════════════════════════════════════════════════════════

class PayoutResponse(BaseModel):
    id: str
    campaign_id: str
    amount: float
    currency: str
    status: str
    stripe_transfer_id: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ════════════════════════════════════════════════════════════════════════════
# Campaign Updates
# ════════════════════════════════════════════════════════════════════════════

class CampaignUpdateCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    content: str = Field(..., min_length=10)


class CampaignUpdateResponse(BaseModel):
    id: str
    campaign_id: str
    author_id: str
    title: str
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ════════════════════════════════════════════════════════════════════════════
# Comments
# ════════════════════════════════════════════════════════════════════════════

class CommentCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)


class CommentResponse(BaseModel):
    id: str
    campaign_id: str
    user_id: str
    user_name: Optional[str] = None
    user_avatar: Optional[str] = None
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ════════════════════════════════════════════════════════════════════════════
# Reports
# ════════════════════════════════════════════════════════════════════════════

class ReportCreate(BaseModel):
    campaign_id: Optional[str] = None
    comment_id: Optional[str] = None
    reason: str  # fraud, inappropriate, spam, misleading, other
    details: Optional[str] = None


class ReportResponse(BaseModel):
    id: str
    campaign_id: Optional[str] = None
    comment_id: Optional[str] = None
    reason: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ════════════════════════════════════════════════════════════════════════════
# Payment Details
# ════════════════════════════════════════════════════════════════════════════

class PaymentDetailCreate(BaseModel):
    account_type: str = "individual"
    account_holder_name: str = Field(..., min_length=1)
    routing_number: str = Field(..., min_length=9, max_length=9)
    account_number: str = Field(..., min_length=4, max_length=17)


class PaymentDetailResponse(BaseModel):
    id: str
    user_id: str
    account_type: str
    account_holder_name: str
    routing_number_last4: Optional[str] = None
    account_number_last4: Optional[str] = None
    is_verified: bool
    is_default: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ════════════════════════════════════════════════════════════════════════════
# Billing Address
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
    id: str
    user_id: str
    full_name: str
    address_line1: str
    address_line2: Optional[str] = None
    city: str
    state: Optional[str] = None
    postal_code: str
    country: str
    is_default: bool
    created_at: datetime

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