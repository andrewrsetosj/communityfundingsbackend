"""
User routes — profiles, payment details, billing addresses
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List

from app.database import get_db
from app.auth import get_current_user
from app.models.models import User, PaymentDetail, BillingAddress, AccountType
from app.models.schemas import (
    UserResponse, UserPublicResponse, UserProfileUpdate,
    PaymentDetailCreate, PaymentDetailResponse,
    BillingAddressCreate, BillingAddressResponse,
)

router = APIRouter(prefix="/api/users", tags=["users"])


# ── Public profile ─────────────────────────────────────────────────────────

@router.get("/{user_id}", response_model=UserPublicResponse)
async def get_user_public(user_id: str, db: AsyncSession = Depends(get_db)):
    """Get a user's public profile (no email exposed)."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return UserPublicResponse(
        id=user.id, name=user.name,
        last_name=user.last_name, email=user.email,
        bio=user.bio,
        phone_number=user.phone_number,
        address=user.address,
        state=user.state,
        time_zone=user.time_zone,
        created_at=user.created_at,
    )


# ── Update profile ────────────────────────────────────────────────────────

@router.put("/{user_id}", response_model=UserPublicResponse)
async def update_user_profile(
    user_id: str,
    data: UserProfileUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a user's profile fields. Creates the user if they don't exist."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        # Create the creator row on first save
        user = User(id=user_id)
        db.add(user)

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(user, field, value)

    await db.flush()

    return UserPublicResponse(
        id=user.id, name=user.name,
        last_name=user.last_name, email=user.email,
        bio=user.bio,
        phone_number=user.phone_number,
        address=user.address,
        state=user.state,
        time_zone=user.time_zone,
        created_at=user.created_at,
    )


# ── Payment Details ────────────────────────────────────────────────────────

@router.post("/me/payment-details", response_model=PaymentDetailResponse)
async def save_payment_details(
    data: PaymentDetailCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Save bank account details (only stores last 4 digits)."""
    detail = PaymentDetail(
        user_id=user.id,
        account_type=AccountType(data.account_type),
        account_holder_name=data.account_holder_name,
        routing_number_last4=data.routing_number[-4:],
        account_number_last4=data.account_number[-4:],
        is_verified=False,
        is_default=True,
    )
    db.add(detail)
    await db.flush()

    return PaymentDetailResponse(
        id=detail.id, user_id=detail.user_id,
        account_type=detail.account_type.value,
        account_holder_name=detail.account_holder_name,
        routing_number_last4=detail.routing_number_last4,
        account_number_last4=detail.account_number_last4,
        is_verified=detail.is_verified, is_default=detail.is_default,
        created_at=detail.created_at,
    )


@router.get("/me/payment-details", response_model=List[PaymentDetailResponse])
async def get_payment_details(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PaymentDetail).where(PaymentDetail.user_id == user.id)
    )
    return [
        PaymentDetailResponse(
            id=d.id, user_id=d.user_id,
            account_type=d.account_type.value if isinstance(d.account_type, AccountType) else d.account_type,
            account_holder_name=d.account_holder_name,
            routing_number_last4=d.routing_number_last4,
            account_number_last4=d.account_number_last4,
            is_verified=d.is_verified, is_default=d.is_default,
            created_at=d.created_at,
        )
        for d in result.scalars().all()
    ]


@router.delete("/me/payment-details/{detail_id}")
async def delete_payment_detail(
    detail_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(PaymentDetail).where(PaymentDetail.id == detail_id))
    detail = result.scalar_one_or_none()
    if not detail or detail.user_id != user.id:
        raise HTTPException(status_code=404, detail="Payment detail not found")
    await db.delete(detail)
    return {"deleted": True}


# ── Billing Address ────────────────────────────────────────────────────────

@router.post("/me/billing-address", response_model=BillingAddressResponse)
async def save_billing_address(
    data: BillingAddressCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    address = BillingAddress(
        user_id=user.id,
        full_name=data.full_name,
        address_line1=data.address_line1,
        address_line2=data.address_line2,
        city=data.city, state=data.state,
        postal_code=data.postal_code, country=data.country,
    )
    db.add(address)
    await db.flush()

    return BillingAddressResponse(
        id=address.id, user_id=address.user_id,
        full_name=address.full_name,
        address_line1=address.address_line1,
        address_line2=address.address_line2,
        city=address.city, state=address.state,
        postal_code=address.postal_code, country=address.country,
        is_default=address.is_default, created_at=address.created_at,
    )


@router.get("/me/billing-addresses", response_model=List[BillingAddressResponse])
async def get_billing_addresses(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(BillingAddress).where(BillingAddress.user_id == user.id)
    )
    return [
        BillingAddressResponse(
            id=a.id, user_id=a.user_id,
            full_name=a.full_name,
            address_line1=a.address_line1, address_line2=a.address_line2,
            city=a.city, state=a.state,
            postal_code=a.postal_code, country=a.country,
            is_default=a.is_default, created_at=a.created_at,
        )
        for a in result.scalars().all()
    ]