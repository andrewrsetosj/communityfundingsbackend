"""
JWT helpers for:
1) Verifying Clerk RS256 tokens
2) Creating/verifying Community Fundings backend HS256 tokens
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

import jwt
from jwt import PyJWKClient, decode as jwt_decode, InvalidTokenError


# ─────────────────────────────────────────────────────────────────────────────
# Clerk JWT verification config
# ─────────────────────────────────────────────────────────────────────────────

CLERK_JWKS_URL = os.getenv("CLERK_JWKS_URL") or os.getenv("CLERK_JWKS_URI")
CLERK_ISS = os.getenv("CLERK_ISS")
CLERK_AUD = os.getenv("CLERK_AUD")
CLERK_PEM_PUBLIC_KEY = os.getenv("CLERK_PEM_PUBLIC_KEY")
DEV_JWT_BYPASS = os.getenv("DEV_JWT_BYPASS", "false").lower() in ("1", "true", "yes")
DEV_JWT_SUB = os.getenv("DEV_JWT_SUB", "dev_user_local")


# ─────────────────────────────────────────────────────────────────────────────
# Backend JWT config
# ─────────────────────────────────────────────────────────────────────────────

BACKEND_JWT_SECRET = os.getenv("BACKEND_JWT_SECRET", "dev-secret-change-me")
BACKEND_JWT_ALGORITHM = os.getenv("BACKEND_JWT_ALGORITHM", "HS256")
BACKEND_JWT_EXPIRES_HOURS = int(os.getenv("BACKEND_JWT_EXPIRES_HOURS", "1"))


# ─────────────────────────────────────────────────────────────────────────────
# Clerk token verification
# ─────────────────────────────────────────────────────────────────────────────

def verify_token(token: str) -> Optional[Dict[str, Any]]:
    """
    Verify a Clerk RS256 JWT and return claims.
    Raises InvalidTokenError on failure.
    """
    if not token:
        raise InvalidTokenError("empty token")

    if DEV_JWT_BYPASS:
        return {
            "sub": DEV_JWT_SUB,
            "email": os.getenv("DEV_JWT_EMAIL", "dev@example.com"),
            "first_name": os.getenv("DEV_JWT_FIRST", "Dev"),
            "last_name": os.getenv("DEV_JWT_LAST", "Local"),
        }

    if CLERK_JWKS_URL:
        try:
            jwk_client = PyJWKClient(CLERK_JWKS_URL)
            signing_key = jwk_client.get_signing_key_from_jwt(token).key

            verify_kwargs = {"algorithms": ["RS256"]}
            if CLERK_AUD:
                verify_kwargs["audience"] = CLERK_AUD

            claims = jwt_decode(token, signing_key, **verify_kwargs)

            if CLERK_ISS and claims.get("iss") != CLERK_ISS:
                raise InvalidTokenError(
                    f"issuer mismatch: expected {CLERK_ISS}, got {claims.get('iss')}"
                )

            return claims
        except Exception as exc:
            print("jwt_utils: JWKS verification failed:", repr(exc))
            raise InvalidTokenError(str(exc)) from exc

    if CLERK_PEM_PUBLIC_KEY:
        try:
            verify_kwargs = {"algorithms": ["RS256"]}
            if CLERK_AUD:
                verify_kwargs["audience"] = CLERK_AUD

            claims = jwt_decode(token, CLERK_PEM_PUBLIC_KEY, **verify_kwargs)

            if CLERK_ISS and claims.get("iss") != CLERK_ISS:
                raise InvalidTokenError(
                    f"issuer mismatch: expected {CLERK_ISS}, got {claims.get('iss')}"
                )

            return claims
        except Exception as exc:
            print("jwt_utils: PEM verification failed:", repr(exc))
            raise InvalidTokenError(str(exc)) from exc

    raise InvalidTokenError(
        "No JWKS URL (CLERK_JWKS_URL/CLERK_JWKS_URI) or CLERK_PEM_PUBLIC_KEY configured"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Backend token create/verify
# ─────────────────────────────────────────────────────────────────────────────

def create_backend_token(creator_id: str, expires_hours: int = BACKEND_JWT_EXPIRES_HOURS) -> str:
    now = datetime.now(timezone.utc)

    payload = {
        "sub": creator_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=expires_hours)).timestamp()),
    }

    return jwt.encode(
        payload,
        BACKEND_JWT_SECRET,
        algorithm=BACKEND_JWT_ALGORITHM,
    )


def verify_backend_token(token: str) -> Dict[str, Any]:
    if not token:
        raise InvalidTokenError("empty token")

    try:
        claims = jwt.decode(
            token,
            BACKEND_JWT_SECRET,
            algorithms=[BACKEND_JWT_ALGORITHM],
        )
        return claims
    except Exception as exc:
        print("jwt_utils: backend token verification failed:", repr(exc))
        raise InvalidTokenError(str(exc)) from exc