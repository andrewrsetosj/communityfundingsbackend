"""
SQLAlchemy Models — Community Fundings
Complete schema for a GoFundMe / Kickstarter-style platform
"""

from sqlalchemy import (
    Column, String, Float, Integer, Boolean, DateTime, Text,
    ForeignKey, Enum, Index, Numeric, UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
import enum
import uuid


def generate_uuid():
    return str(uuid.uuid4())


# ════════════════════════════════════════════════════════════════════════════
# Enums
# ════════════════════════════════════════════════════════════════════════════

class CampaignStatus(str, enum.Enum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    ACTIVE = "active"
    FUNDED = "funded"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    SUSPENDED = "suspended"


class DonationStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"


class RefundStatus(str, enum.Enum):
    REQUESTED = "requested"
    APPROVED = "approved"
    PROCESSED = "processed"
    DENIED = "denied"


class PayoutStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    PAID = "paid"
    FAILED = "failed"


class AccountType(str, enum.Enum):
    INDIVIDUAL = "individual"
    BUSINESS = "business"


class ReportReason(str, enum.Enum):
    FRAUD = "fraud"
    INAPPROPRIATE = "inappropriate"
    SPAM = "spam"
    MISLEADING = "misleading"
    OTHER = "other"


class ReportStatus(str, enum.Enum):
    OPEN = "open"
    REVIEWING = "reviewing"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


# ════════════════════════════════════════════════════════════════════════════
# Users
# ════════════════════════════════════════════════════════════════════════════

class User(Base):
    __tablename__ = "users"

    id = Column(String(50), primary_key=True, default=generate_uuid)
    email = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    hashed_password = Column(String(255), nullable=False)
    avatar_url = Column(String(500), nullable=True)
    bio = Column(Text, nullable=True)
    email_verified = Column(Boolean, default=False)
    is_admin = Column(Boolean, default=False)

    # Stripe
    stripe_customer_id = Column(String(255), unique=True, nullable=True)
    stripe_connect_account_id = Column(String(255), unique=True, nullable=True)
    stripe_connect_onboarded = Column(Boolean, default=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    campaigns = relationship("Campaign", back_populates="creator", lazy="selectin")
    donations = relationship("Donation", back_populates="donor", lazy="selectin")
    payment_details = relationship("PaymentDetail", back_populates="user", lazy="selectin")
    billing_addresses = relationship("BillingAddress", back_populates="user", lazy="selectin")
    comments = relationship("Comment", back_populates="user", lazy="selectin")


# ════════════════════════════════════════════════════════════════════════════
# Campaigns (Projects)
# ════════════════════════════════════════════════════════════════════════════

class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(String(50), primary_key=True, default=generate_uuid)
    title = Column(String(200), nullable=False)
    slug = Column(String(220), unique=True, nullable=False, index=True)
    short_description = Column(String(300), nullable=True)
    description = Column(Text, nullable=False)
    goal_amount = Column(Numeric(12, 2), nullable=False)
    raised_amount = Column(Numeric(12, 2), default=0.00)
    image_url = Column(String(500), nullable=True)
    video_url = Column(String(500), nullable=True)
    creator_id = Column(String(50), ForeignKey("users.id"), nullable=False)
    status = Column(
        Enum(CampaignStatus), default=CampaignStatus.DRAFT, nullable=False, index=True
    )
    donors_count = Column(Integer, default=0)
    category = Column(String(100), nullable=True, index=True)
    location = Column(String(255), nullable=True)
    end_date = Column(DateTime(timezone=True), nullable=True)
    is_featured = Column(Boolean, default=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    creator = relationship("User", back_populates="campaigns")
    donations = relationship("Donation", back_populates="campaign", lazy="selectin")
    updates = relationship("CampaignUpdate", back_populates="campaign", lazy="selectin",
                           order_by="CampaignUpdate.created_at.desc()")
    comments = relationship("Comment", back_populates="campaign", lazy="selectin",
                            order_by="Comment.created_at.desc()")
    payouts = relationship("Payout", back_populates="campaign", lazy="selectin")

    __table_args__ = (
        Index("idx_campaigns_status_created", "status", "created_at"),
        Index("idx_campaigns_category", "category"),
        Index("idx_campaigns_creator", "creator_id"),
    )


# ════════════════════════════════════════════════════════════════════════════
# Donations
# ════════════════════════════════════════════════════════════════════════════

class Donation(Base):
    __tablename__ = "donations"

    id = Column(String(50), primary_key=True, default=generate_uuid)
    campaign_id = Column(String(50), ForeignKey("campaigns.id"), nullable=False)
    donor_id = Column(String(50), ForeignKey("users.id"), nullable=True)
    donor_name = Column(String(255), default="Anonymous")
    donor_email = Column(String(255), nullable=True)
    is_anonymous = Column(Boolean, default=False)
    message = Column(Text, nullable=True)  # message left with donation

    # Money
    amount = Column(Numeric(12, 2), nullable=False)
    platform_fee = Column(Numeric(12, 2), nullable=True)
    processing_fee = Column(Numeric(12, 2), nullable=True)
    net_amount = Column(Numeric(12, 2), nullable=True)
    currency = Column(String(3), default="usd")

    # Stripe references
    stripe_payment_intent_id = Column(String(255), unique=True, nullable=True, index=True)
    stripe_checkout_session_id = Column(String(255), unique=True, nullable=True, index=True)
    stripe_charge_id = Column(String(255), nullable=True)

    # Refund tracking
    refund_amount = Column(Numeric(12, 2), nullable=True)
    stripe_refund_id = Column(String(255), nullable=True)

    status = Column(
        Enum(DonationStatus), default=DonationStatus.PENDING, nullable=False, index=True
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    campaign = relationship("Campaign", back_populates="donations")
    donor = relationship("User", back_populates="donations")
    refund_requests = relationship("RefundRequest", back_populates="donation", lazy="selectin")

    __table_args__ = (
        Index("idx_donations_campaign_status", "campaign_id", "status"),
        Index("idx_donations_donor", "donor_id"),
    )


# ════════════════════════════════════════════════════════════════════════════
# Refund Requests
# ════════════════════════════════════════════════════════════════════════════

class RefundRequest(Base):
    __tablename__ = "refund_requests"

    id = Column(String(50), primary_key=True, default=generate_uuid)
    donation_id = Column(String(50), ForeignKey("donations.id"), nullable=False)
    requester_id = Column(String(50), ForeignKey("users.id"), nullable=False)
    reason = Column(Text, nullable=False)
    amount = Column(Numeric(12, 2), nullable=True)  # null = full refund
    status = Column(Enum(RefundStatus), default=RefundStatus.REQUESTED, nullable=False)
    admin_notes = Column(Text, nullable=True)
    stripe_refund_id = Column(String(255), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    donation = relationship("Donation", back_populates="refund_requests")


# ════════════════════════════════════════════════════════════════════════════
# Payouts (Creator withdrawals via Stripe Connect)
# ════════════════════════════════════════════════════════════════════════════

class Payout(Base):
    __tablename__ = "payouts"

    id = Column(String(50), primary_key=True, default=generate_uuid)
    campaign_id = Column(String(50), ForeignKey("campaigns.id"), nullable=False)
    creator_id = Column(String(50), ForeignKey("users.id"), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(3), default="usd")
    status = Column(Enum(PayoutStatus), default=PayoutStatus.PENDING, nullable=False)
    stripe_transfer_id = Column(String(255), nullable=True)
    stripe_payout_id = Column(String(255), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    campaign = relationship("Campaign", back_populates="payouts")


# ════════════════════════════════════════════════════════════════════════════
# Campaign Updates (Creator posts to backers)
# ════════════════════════════════════════════════════════════════════════════

class CampaignUpdate(Base):
    __tablename__ = "campaign_updates"

    id = Column(String(50), primary_key=True, default=generate_uuid)
    campaign_id = Column(String(50), ForeignKey("campaigns.id"), nullable=False)
    author_id = Column(String(50), ForeignKey("users.id"), nullable=False)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    campaign = relationship("Campaign", back_populates="updates")

    __table_args__ = (
        Index("idx_updates_campaign", "campaign_id"),
    )


# ════════════════════════════════════════════════════════════════════════════
# Comments (Donor messages on campaigns)
# ════════════════════════════════════════════════════════════════════════════

class Comment(Base):
    __tablename__ = "comments"

    id = Column(String(50), primary_key=True, default=generate_uuid)
    campaign_id = Column(String(50), ForeignKey("campaigns.id"), nullable=False)
    user_id = Column(String(50), ForeignKey("users.id"), nullable=False)
    content = Column(Text, nullable=False)
    is_hidden = Column(Boolean, default=False)  # admin can hide

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    campaign = relationship("Campaign", back_populates="comments")
    user = relationship("User", back_populates="comments")

    __table_args__ = (
        Index("idx_comments_campaign", "campaign_id"),
    )


# ════════════════════════════════════════════════════════════════════════════
# Reports (Flag campaigns / comments)
# ════════════════════════════════════════════════════════════════════════════

class Report(Base):
    __tablename__ = "reports"

    id = Column(String(50), primary_key=True, default=generate_uuid)
    reporter_id = Column(String(50), ForeignKey("users.id"), nullable=False)
    campaign_id = Column(String(50), ForeignKey("campaigns.id"), nullable=True)
    comment_id = Column(String(50), ForeignKey("comments.id"), nullable=True)
    reason = Column(Enum(ReportReason), nullable=False)
    details = Column(Text, nullable=True)
    status = Column(Enum(ReportStatus), default=ReportStatus.OPEN, nullable=False)
    admin_notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)


# ════════════════════════════════════════════════════════════════════════════
# Payment Details (Bank account info for receiving payouts)
# ════════════════════════════════════════════════════════════════════════════

class PaymentDetail(Base):
    __tablename__ = "payment_details"

    id = Column(String(50), primary_key=True, default=generate_uuid)
    user_id = Column(String(50), ForeignKey("users.id"), nullable=False)
    account_type = Column(Enum(AccountType), default=AccountType.INDIVIDUAL)
    account_holder_name = Column(String(255), nullable=False)
    routing_number_last4 = Column(String(4), nullable=True)
    account_number_last4 = Column(String(4), nullable=True)
    stripe_connect_account_id = Column(String(255), nullable=True)
    is_verified = Column(Boolean, default=False)
    is_default = Column(Boolean, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="payment_details")


# ════════════════════════════════════════════════════════════════════════════
# Billing Addresses
# ════════════════════════════════════════════════════════════════════════════

class BillingAddress(Base):
    __tablename__ = "billing_addresses"

    id = Column(String(50), primary_key=True, default=generate_uuid)
    user_id = Column(String(50), ForeignKey("users.id"), nullable=False)
    full_name = Column(String(255), nullable=False)
    address_line1 = Column(String(255), nullable=False)
    address_line2 = Column(String(255), nullable=True)
    city = Column(String(100), nullable=False)
    state = Column(String(100), nullable=True)
    postal_code = Column(String(20), nullable=False)
    country = Column(String(2), default="US")
    is_default = Column(Boolean, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="billing_addresses")


# ════════════════════════════════════════════════════════════════════════════
# Webhook Events (Stripe idempotency)
# ════════════════════════════════════════════════════════════════════════════

class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id = Column(String(50), primary_key=True, default=generate_uuid)
    stripe_event_id = Column(String(255), unique=True, nullable=False, index=True)
    event_type = Column(String(100), nullable=False)
    payload = Column(Text, nullable=True)
    processed = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())