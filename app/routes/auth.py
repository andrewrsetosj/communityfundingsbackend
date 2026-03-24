"""
Auth routes — register, login, profile, password change
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from app.database import get_db
from app.auth import hash_password, verify_password, create_access_token, get_current_user
from app.models.models import User
from app.models.schemas import (
    RegisterRequest, LoginRequest, TokenResponse,
    UserResponse, UserUpdate, PasswordChangeRequest,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse)
async def register(data: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """Create a new account and return JWT."""
    existing = await db.execute(select(User).where(User.email == data.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(
        email=data.email,
        name=data.name,
        hashed_password=hash_password(data.password),
    )
    db.add(user)
    await db.flush()

    token = create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        user=UserResponse(
            id=user.id, email=user.email, name=user.name,
            email_verified=False, stripe_connect_onboarded=False,
            created_at=user.created_at,
        ),
    )


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Authenticate with email + password, return JWT."""
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        user=UserResponse(
            id=user.id, email=user.email, name=user.name,
            avatar_url=user.avatar_url, bio=user.bio,
            email_verified=user.email_verified,
            stripe_connect_onboarded=user.stripe_connect_onboarded,
            created_at=user.created_at,
        ),
    )


@router.get("/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user)):
    """Get current authenticated user profile."""
    return UserResponse(
        id=user.id, email=user.email, name=user.name,
        avatar_url=user.avatar_url, bio=user.bio,
        email_verified=user.email_verified,
        stripe_connect_onboarded=user.stripe_connect_onboarded,
        created_at=user.created_at,
    )

@router.put("/me", response_model=UserResponse)
async def update_me(
    data: UserUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update current user's profile."""
    if data.name is not None:
        user.name = data.name
    if data.bio is not None:
        user.bio = data.bio
    if data.avatar_url is not None:
        user.avatar_url = data.avatar_url
    await db.flush()

    return UserResponse(
        id=user.id, email=user.email, name=user.name,
        avatar_url=user.avatar_url, bio=user.bio,
        email_verified=user.email_verified,
        stripe_connect_onboarded=user.stripe_connect_onboarded,
        created_at=user.created_at,
    )


@router.post("/change-password")
async def change_password(
    data: PasswordChangeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Change the authenticated user's password."""
    if not verify_password(data.current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    user.hashed_password = hash_password(data.new_password)
    await db.flush()
    return {"message": "Password updated"}
    
from pydantic import BaseModel as PydanticBaseModel

class ClerkSyncRequest(PydanticBaseModel):
    clerk_id: str
    email: str
    name: str


@router.post("/clerk-sync")
async def clerk_sync(data: ClerkSyncRequest, db: AsyncSession = Depends(get_db)):
    """Bridge Clerk frontend auth → backend JWT. Creates user if needed."""
    import traceback
    try:
        print(f"[clerk-sync] clerk_id={data.clerk_id}, email={data.email}, name={data.name}")

        # Look up by Clerk ID first, then fall back to email
        result = await db.execute(select(User).where(User.id == data.clerk_id))
        user = result.scalar_one_or_none()
        print(f"[clerk-sync] lookup by clerk_id: {'found' if user else 'not found'}")

        if not user:
            # Check if there's an existing row by email (from before clerk_id was stored)
            result = await db.execute(select(User).where(User.email == data.email))
            user = result.scalar_one_or_none()
            print(f"[clerk-sync] lookup by email: {'found id=' + str(user.id) if user else 'not found'}")

            if user:
                # Update the existing row's creator_id to the Clerk ID via raw SQL
                old_id = user.id
                await db.execute(
                    text("UPDATE creators SET creator_id = :new_id WHERE creator_id = :old_id"),
                    {"new_id": data.clerk_id, "old_id": old_id},
                )
                await db.flush()
                # Re-fetch with the new ID
                result = await db.execute(select(User).where(User.id == data.clerk_id))
                user = result.scalar_one_or_none()
                print(f"[clerk-sync] after PK update: {'found' if user else 'NOT FOUND'}")
            else:
                user = User(
                    id=data.clerk_id,
                    email=data.email,
                    name=data.name,
                    hashed_password=hash_password(f"clerk_synced_{data.email}"),
                )
                db.add(user)
                await db.flush()
                print(f"[clerk-sync] created new user id={user.id}")

        token = create_access_token(user.id)
        print(f"[clerk-sync] success, returning token for user={user.id}")
        return {"access_token": token, "user_id": user.id}

    except Exception as e:
        print(f"[clerk-sync] ERROR: {e}")
        traceback.print_exc()
        raise
