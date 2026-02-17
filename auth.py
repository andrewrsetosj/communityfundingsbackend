# # app/auth.py
# """
# Authentication — JWT tokens + password hashing
# """

# import os
# from datetime import datetime, timedelta, timezone
# from typing import Optional

# import bcrypt
# import jwt
# from fastapi import Depends, HTTPException
# from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
# from jwt.exceptions import PyJWTError as JWTError
# from sqlalchemy import select
# from sqlalchemy.ext.asyncio import AsyncSession

# from app.database import get_db
# from app.models.models import User

# # Config
# SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-secret-change-me-in-production")
# ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
# ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

# security = HTTPBearer(auto_error=False)


# # ── Password helpers ───────────────────────────────────────────────────────

# def hash_password(password: str) -> str:
#     return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


# def verify_password(plain_password: str, hashed_password: str) -> bool:
#     return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


# # ── JWT helpers ────────────────────────────────────────────────────────────

# def create_access_token(user_id: str, expires_delta: Optional[timedelta] = None) -> str:
#     expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
#     payload = {"sub": str(user_id), "exp": expire, "iat": datetime.now(timezone.utc)}
#     return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


# def decode_token(token: str) -> Optional[str]:
#     """Returns user_id (sub) or None."""
#     try:
#         payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
#         sub = payload.get("sub")
#         return str(sub) if sub else None
#     except JWTError:
#         return None


# # ── FastAPI Dependencies ───────────────────────────────────────────────────

# async def get_current_user(
#     credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
#     db: AsyncSession = Depends(get_db),
# ) -> User:
#     """Require a valid JWT. Returns the User ORM object."""
#     if not credentials:
#         raise HTTPException(status_code=401, detail="Not authenticated")

#     user_id = decode_token(credentials.credentials)
#     if not user_id:
#         raise HTTPException(status_code=401, detail="Invalid or expired token")

#     result = await db.execute(select(User).where(User.id == user_id))
#     user = result.scalar_one_or_none()
#     if not user:
#         raise HTTPException(status_code=401, detail="User not found")
#     return user


# async def get_optional_user(
#     credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
#     db: AsyncSession = Depends(get_db),
# ) -> Optional[User]:
#     """Same as above but returns None instead of 401 if no token."""
#     if not credentials:
#         return None
#     user_id = decode_token(credentials.credentials)
#     if not user_id:
#         return None
#     result = await db.execute(select(User).where(User.id == user_id))
#     return result.scalar_one_or_none()


# async def require_admin(user: User = Depends(get_current_user)) -> User:
#     """Require the current user to be an admin."""
#     if not user.is_admin:
#         raise HTTPException(status_code=403, detail="Admin access required")
#     return user

# app/auth.py  — add these imports at the top (merge with existing imports)
import os
from jwt import PyJWKClient, InvalidTokenError, decode as jwt_decode, get_unverified_header

# --- Add these helper functions near your existing JWT helpers ---

CLERK_JWKS_URL = os.getenv("CLERK_JWKS_URL")  # e.g. set to Clerk's jwks endpoint
CLERK_ISS = os.getenv("CLERK_ISS")  # optional issuer check
CLERK_AUD = os.getenv("CLERK_AUD")  # optional audience check

def decode_local_hs256(token: str) -> Optional[str]:
    """Existing behavior: decode local HS256 tokens."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return str(payload.get("sub"))
    except JWTError:
        return None

def decode_clerk_rs256(token: str) -> Optional[str]:
    """Validate Clerk RS256 JWT using Clerk JWKS and return the 'sub' (user id)."""
    if not CLERK_JWKS_URL:
        # JWKS URL not configured — we can't verify
        return None

    try:
        # fetch public key for this token's kid
        jwk_client = PyJWKClient(CLERK_JWKS_URL)
        signing_key = jwk_client.get_signing_key_from_jwt(token).key

        # build verification kwargs
        verify_kwargs = {
            "algorithms": ["RS256"],
        }
        # optionally verify issuer/audience if provided
        if CLERK_ISS:
            verify_kwargs["issuer"] = CLERK_ISS
        if CLERK_AUD:
            verify_kwargs["audience"] = CLERK_AUD

        payload = jwt_decode(token, signing_key, **verify_kwargs)
        return str(payload.get("sub"))
    except Exception:
        return None

def decode_token(token: str) -> Optional[str]:
    """
    Try to decode token; supports:
      - local HS256 tokens (your existing tokens)
      - Clerk RS256 tokens (via JWKS)
    Returns user_id string or None.
    """
    # Quick guard
    if not token:
        return None

    # Try to inspect the header to decide algorithm
    try:
        header = get_unverified_header(token)
        alg = header.get("alg", "").upper()
    except Exception:
        alg = ""

    # If it's RS256 — assume an external issuer like Clerk
    if alg == "RS256":
        return decode_clerk_rs256(token)

    # fallback: try local HS256 decode (keeps existing behavior)
    return decode_local_hs256(token)
