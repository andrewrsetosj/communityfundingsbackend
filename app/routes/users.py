"""
User routes — profiles, payment details, billing addresses
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from typing import List
import re

from app.database import get_db
from app.auth import get_current_user
import db as db_mod
from app.models.models import User, PaymentDetail, BillingAddress, AccountType
from app.models.schemas import (
    UserResponse, UserPublicResponse, UserProfileUpdate,
    PaymentDetailCreate, PaymentDetailResponse,
    BillingAddressCreate, BillingAddressResponse,
)

router = APIRouter(prefix="/api/users", tags=["users"])

USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 30
USERNAME_PATTERN = re.compile(r"^[a-z_]+$")


BANNED_WORD_PATTERNS = [
    r"\bfuck(?:ing|er|ed|s)?\b",
    r"\bshit(?:ty|s)?\b",
    r"\bbitch(?:es)?\b",
    r"\basshole(?:s)?\b",
    r"\bdamn\b",
]


def _normalize_for_profanity_check(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-zA-Z0-9\s]", " ", value.lower())).strip()


def _contains_foul_language(value: str | None) -> bool:
    if not value:
        return False
    normalized = _normalize_for_profanity_check(value)
    return any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in BANNED_WORD_PATTERNS)


def _validate_profile_text_field(value: str | None, field_label: str) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if cleaned and _contains_foul_language(cleaned):
        raise HTTPException(status_code=400, detail=f"Please remove profanity from {field_label} and try again")
    return cleaned


def _normalize_website(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().lower()
    if not cleaned:
        return ""
    candidate = cleaned if cleaned.startswith(("http://", "https://")) else f"https://{cleaned}"
    if not re.match(r"^https?://[a-z0-9.-]+\.[a-z]{2,}(/.*)?$", candidate):
        raise HTTPException(status_code=400, detail="Website must be a valid link")
    return cleaned


def _public_user_response(user: User) -> UserPublicResponse:
    return UserPublicResponse(
        id=user.id,
        username=user.username or user.id,
        name=user.name,
        last_name=user.last_name,
        email=user.email,
        bio=user.bio,
        phone_number=user.phone_number,
        address=user.address,
        state=user.state,
        time_zone=user.time_zone,
        website=user.website,
        created_at=user.created_at,
    )


async def _validate_username(db: AsyncSession, username: str, current_user_id: str) -> str:
    candidate = username.strip().lower()
    if not candidate:
        return current_user_id
    if _contains_foul_language(candidate):
        raise HTTPException(status_code=400, detail="Please remove profanity from username and try again")
    if " " in candidate:
        raise HTTPException(status_code=400, detail="Username cannot contain spaces")
    if len(candidate) < USERNAME_MIN_LENGTH or len(candidate) > USERNAME_MAX_LENGTH:
        raise HTTPException(status_code=400, detail=f"Username must be between {USERNAME_MIN_LENGTH} and {USERNAME_MAX_LENGTH} characters")
    if not USERNAME_PATTERN.fullmatch(candidate):
        raise HTTPException(status_code=400, detail="Username can only contain lowercase letters and underscores.")
    conflict_stmt = select(User.id).where(
        User.id != current_user_id,
        or_(
            func.lower(User.username) == candidate.lower(),
            func.lower(User.id) == candidate.lower(),
        )
    )
    conflict = (await db.execute(conflict_stmt)).scalar_one_or_none()
    if conflict:
        raise HTTPException(status_code=409, detail="That username is already taken")
    return candidate


@router.get("/{user_id}", response_model=UserPublicResponse)
async def get_user_public(user_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(User).where(
            or_(User.id == user_id, False)
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.username:
        user.username = user.id
        await db.flush()
    return _public_user_response(user)


@router.put("/{user_id}", response_model=UserPublicResponse)
async def update_user_profile(
    user_id: str,
    data: UserProfileUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        user = User(id=user_id, username=user_id)
        db.add(user)
        await db.flush()

    payload = data.model_dump(exclude_unset=True)

    if "username" in payload and payload["username"] is not None:
        user.username = await _validate_username(db, payload.pop("username"), user_id)
    elif not user.username:
        user.username = user.id

    field_labels = {
        "name": "first name",
        "last_name": "last name",
        "bio": "about you",
        "phone_number": "contact number",
        "address": "address",
        "state": "state",
        "time_zone": "time zone",
    }

    for field, label in field_labels.items():
        if field in payload:
            payload[field] = _validate_profile_text_field(payload[field], label)

    if "website" in payload:
        payload["website"] = _normalize_website(payload["website"])
        if _contains_foul_language(payload["website"]):
            raise HTTPException(status_code=400, detail="Please remove profanity from website and try again")

    for field, value in payload.items():
        setattr(user, field, value)

    await db.flush()
    return _public_user_response(user)


@router.post("/me/payment-details", response_model=PaymentDetailResponse)
async def save_payment_details(
    data: PaymentDetailCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
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


@router.get("/me/interests")
async def get_my_interests(user: User = Depends(get_current_user)):
    pool = await db_mod.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT i.interest_id, i.name
            FROM creator_interests ci
            JOIN interests i ON i.interest_id = ci.interest_id
            WHERE ci.creator_id = $1
            ORDER BY i.name
            """,
            user.id,
        )
        return [{"interest_id": r["interest_id"], "name": r["name"]} for r in rows]


@router.put("/me/interests")
async def set_my_interests(
    data: dict,
    user: User = Depends(get_current_user),
):
    names = data.get("interest_names", [])
    if not isinstance(names, list):
        raise HTTPException(status_code=400, detail="interest_names must be a list")

    pool = await db_mod.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM creator_interests WHERE creator_id = $1",
                user.id,
            )
            for name in names:
                interest = await conn.fetchrow(
                    "SELECT interest_id FROM interests WHERE name = $1",
                    name,
                )
                if interest:
                    await conn.execute(
                        "INSERT INTO creator_interests (creator_id, interest_id) VALUES ($1, $2)",
                        user.id, interest["interest_id"],
                    )
    return {"saved": len(names)}
