"""
Auth routes — register, login, profile, password change
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from pydantic import BaseModel
from typing import Optional

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
        user_type=1,
    )
    db.add(user)
    await db.flush()

    token = create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        user=UserResponse(
            id=user.id,
            email=user.email,
            name=user.name,
            avatar_url=user.avatar_url,
            bio=user.bio,
            email_verified=user.email_verified,
            stripe_connect_onboarded=user.stripe_connect_onboarded,
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
            id=user.id,
            email=user.email,
            name=user.name,
            avatar_url=user.avatar_url,
            bio=user.bio,
            email_verified=user.email_verified,
            stripe_connect_onboarded=user.stripe_connect_onboarded,
            created_at=user.created_at,
        ),
    )


@router.get("/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user)):
    """Get current authenticated user profile."""
    return UserResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        avatar_url=user.avatar_url,
        bio=user.bio,
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
        id=user.id,
        email=user.email,
        name=user.name,
        avatar_url=user.avatar_url,
        bio=user.bio,
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


class ClerkSyncRequest(BaseModel):
    clerk_id: str
    email: Optional[str] = None
    name: Optional[str] = None
    image_url: Optional[str] = None


async def _repoint_foreign_keys_to_creator_id(
    db: AsyncSession, *, old_id: str, new_id: str
) -> None:
    """
    Point every FK that referenced old_id at new_id.
    Requires a `creators` row with creator_id = new_id to already exist (FK checks).
    """
    result = await db.execute(
        text(
            """
            SELECT DISTINCT c.conrelid::regclass::text AS tbl, a.attname::text AS col
            FROM pg_constraint AS c
            JOIN unnest(c.conkey) AS ck(attnum) ON TRUE
            JOIN pg_attribute AS a ON a.attrelid = c.conrelid AND a.attnum = ck.attnum
            WHERE c.confrelid = 'public.creators'::regclass
              AND c.contype = 'f'
            """
        )
    )
    for table_qname, column_name in result.all():
        # e.g. "public.campaigns" — strip schema for UPDATE ... public."campaigns"
        if "." in table_qname:
            _, table_name = table_qname.rsplit(".", 1)
        else:
            table_name = table_qname
        if table_name.strip('"') == "creators":
            continue
        await db.execute(
            text(
                f'UPDATE {table_qname} SET "{column_name}" = :new_id '
                f'WHERE "{column_name}" = :old_id'
            ),
            {"new_id": new_id, "old_id": old_id},
        )


@router.post("/clerk-sync")
async def clerk_sync(data: ClerkSyncRequest, db: AsyncSession = Depends(get_db)):
    """Bridge Clerk frontend auth → backend JWT. Creates user if needed."""
    import traceback

    try:
        print(
            f"[clerk-sync] clerk_id={data.clerk_id}, "
            f"email={data.email}, name={data.name}, image_url={data.image_url}"
        )

        # Look up by Clerk ID first
        result = await db.execute(select(User).where(User.id == data.clerk_id))
        user = result.scalar_one_or_none()
        print(f"[clerk-sync] lookup by clerk_id: {'found' if user else 'not found'}")

        if not user:
            # Fall back to email if a pre-existing row exists
            result = await db.execute(select(User).where(User.email == data.email))
            user = result.scalar_one_or_none()
            print(f"[clerk-sync] lookup by email: {'found id=' + str(user.id) if user else 'not found'}")

            if user:
                old_id = user.id
                if old_id != data.clerk_id:
                    clash = await db.execute(
                        select(User).where(User.id == data.clerk_id)
                    )
                    if clash.scalar_one_or_none():
                        raise HTTPException(
                            status_code=409,
                            detail="Another creators row already uses this Clerk id; automatic merge is not supported.",
                        )
                    # Unique on email/username: clear old row so the new row can take them.
                    user.email = None
                    user.username = None
                    await db.flush()

                    new_user = User(
                        id=data.clerk_id,
                        email=data.email,
                        name=data.name if data.name is not None else user.name,
                        last_name=user.last_name,
                        hashed_password=user.hashed_password,
                        bio=user.bio,
                        avatar_url=data.image_url or user.avatar_url,
                        user_type=user.user_type,
                        website=user.website,
                        phone_number=user.phone_number,
                        address=user.address,
                        state=user.state,
                        time_zone=user.time_zone,
                    )
                    db.add(new_user)
                    await db.flush()

                    await _repoint_foreign_keys_to_creator_id(
                        db, old_id=old_id, new_id=data.clerk_id
                    )
                    await db.delete(user)
                    await db.flush()
                    user = new_user
                    print(f"[clerk-sync] merged email account {old_id} -> {data.clerk_id}")
            else:
                user = User(
                    id=data.clerk_id,
                    email=data.email,
                    name=data.name,
                    hashed_password=hash_password(f"clerk_synced_{data.email or data.clerk_id}"),
                    user_type=1,
                )
                db.add(user)
                await db.flush()
                print(f"[clerk-sync] created new user id={user.id}")

        # Keep backend user info updated from Clerk
        user.user_type = 1  # always individual for Clerk users

        if data.email:
            user.email = data.email

        if data.image_url:
            user.avatar_url = data.image_url

        await db.flush()

        token = create_access_token(user.id)
        print(f"[clerk-sync] success, returning token for user={user.id}")
        return {"access_token": token, "user_id": user.id}

    except Exception as e:
        print(f"[clerk-sync] ERROR: {e}")
        traceback.print_exc()
        raise


class RegisterBusinessRequest(BaseModel):
    email: str
    name: str
    password: str
    owner_id: Optional[str] = None


@router.post("/register-business")
async def register_business(data: RegisterBusinessRequest, db: AsyncSession = Depends(get_db)):
    """Create a business account (no Clerk) and optionally add owner to organization_members."""
    existing = await db.execute(select(User).where(User.email == data.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(
        email=data.email,
        name=data.name,
        hashed_password=hash_password(data.password),
        user_type=0,
    )
    db.add(user)
    await db.flush()

    if data.owner_id:
        await db.execute(
            text(
                "INSERT INTO organization_members "
                "(member_id, organization_id, role, added_by) "
                "VALUES (:member_id, :organization_id, :role, :added_by)"
            ),
            {
                "member_id": data.owner_id,
                "organization_id": user.id,
                "role": "owner",
                "added_by": data.owner_id,
            },
        )

    token = create_access_token(user.id)
    return {"access_token": token, "user": {"id": user.id, "email": user.email, "name": user.name}}