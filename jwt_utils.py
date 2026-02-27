# # jwt_utils.py
# import os
# import time
# from dotenv import load_dotenv
# import requests
# import jwt
# from jwt import PyJWKClient, InvalidTokenError, decode as jwt_decode

# load_dotenv()

# CLERK_PEM_PUBLIC_KEY = os.getenv("CLERK_PEM_PUBLIC_KEY")
# CLERK_JWKS_URI = os.getenv("CLERK_JWKS_URI")
# # implement later
# # JWT_EXPECTED_ISSUER = os.getenv("JWT_EXPECTED_ISSUER")
# # JWT_EXPECTED_AUDIENCE = os.getenv("JWT_EXPECTED_AUDIENCE")
# JWKS_CACHE_TTL = int(os.getenv("JWKS_CACHE_TTL", "300"))

# # Simple JWKS cache (fallback if you don't use PyJWKClient repeatedly)
# _jwks_cache = {"fetched_at": 0, "jwks": None}

# def _get_jwk_client(jwks_uri):
#     return PyJWKClient(jwks_uri)

# def verify_token_with_jwks(token: str):
#     if not CLERK_JWKS_URI:
#         raise ValueError("CLERK_JWKS_URI not configured")
#     # auto caching and rotation w PyJWTClient
#     jwk_client = _get_jwk_client(CLERK_JWKS_URI)
#     signing_key = jwk_client.get_signing_key_from_jwt(token)
#     key = signing_key.key
#     # verify with extracted key
#     return jwt.decode(
#         token,
#         key=key,
#         algorithms=["RS256"],
#         # issuer=JWT_EXPECTED_ISSUER or None,
#         # audience=JWT_EXPECTED_AUDIENCE or None,
#     )
#     return payload

# def verify_token_with_pem(token: str):
#     if not CLERK_PEM_PUBLIC_KEY:
#         raise ValueError("CLERK_PEM_PUBLIC_KEY not configured")
#     return jwt.decode(
#         token,
#         CLERK_PEM_PUBLIC_KEY,
#         algorithms=["RS256"],
#         # issuer=JWT_EXPECTED_ISSUER or None,
#         # audience=JWT_EXPECTED_AUDIENCE or None,
#     )
#     return payload

# def verify_token(token: str):
#     last_err = None
#     # Prefer JWKS (rotation-safe)
#     if CLERK_JWKS_URI:
#         try:
#             return verify_token_with_jwks(token)
#         except Exception as e:
#             last_err = e
#     # Fallback to static PEM if available
#     if CLERK_PEM_PUBLIC_KEY:
#         try:
#             return verify_token_with_pem(token)
#         except Exception as e:
#             last_err = e
#     raise InvalidTokenError(f"Token verification failed: {last_err}")
# jwt_utils.py
# jwt_utils.py
"""
JWT verification helpers for Clerk tokens.

Behavior:
- If DEV_JWT_BYPASS env var is truthy, returns a fake claims dict (local dev only).
- Otherwise tries to verify using JWKS URL (CLERK_JWKS_URL or CLERK_JWKS_URI).
- If JWKS is missing but CLERK_PEM_PUBLIC_KEY is present, uses that PEM as a fallback.
- Raises jwt.InvalidTokenError on any verification problem.
"""

import os
from typing import Optional, Dict, Any
from jwt import PyJWKClient, decode as jwt_decode, InvalidTokenError

# Config from environment (support both names)
CLERK_JWKS_URL = os.getenv("CLERK_JWKS_URL") or os.getenv("CLERK_JWKS_URI")
CLERK_ISS = os.getenv("CLERK_ISS")
CLERK_AUD = os.getenv("CLERK_AUD")
CLERK_PEM_PUBLIC_KEY = os.getenv("CLERK_PEM_PUBLIC_KEY")  # optional fallback PEM
DEV_JWT_BYPASS = os.getenv("DEV_JWT_BYPASS", "false").lower() in ("1", "true", "yes")
DEV_JWT_SUB = os.getenv("DEV_JWT_SUB", "dev_user_local")  # subject used by dev bypass

def verify_token(token: str) -> Optional[Dict[str, Any]]:
    """
    Verify an RS256 JWT and return the claims dict.
    Raises InvalidTokenError on failure.

    For local development you can set DEV_JWT_BYPASS=true to bypass JWKS validation
    (returns a simple claims dict with 'sub' set to DEV_JWT_SUB). Do NOT enable
    this in production.
    """
    if not token:
        raise InvalidTokenError("empty token")

    # Dev bypass (local testing only)
    if DEV_JWT_BYPASS:
        # Minimal claims your app expects; modify as needed for local testing.
        return {
            "sub": DEV_JWT_SUB,
            "email": os.getenv("DEV_JWT_EMAIL", "dev@example.com"),
            "first_name": os.getenv("DEV_JWT_FIRST", "Dev"),
            "last_name": os.getenv("DEV_JWT_LAST", "Local"),
        }

    # Prefer JWKS URL (supports env names CLERK_JWKS_URL or CLERK_JWKS_URI)
    if CLERK_JWKS_URL:
        try:
            jwk_client = PyJWKClient(CLERK_JWKS_URL)
            signing_key = jwk_client.get_signing_key_from_jwt(token).key
            verify_kwargs = {"algorithms": ["RS256"]}
            if CLERK_AUD:
                verify_kwargs["audience"] = CLERK_AUD

            claims = jwt_decode(token, signing_key, **verify_kwargs)
            if CLERK_ISS and claims.get("iss") != CLERK_ISS:
                raise InvalidTokenError(f"issuer mismatch: expected {CLERK_ISS}, got {claims.get('iss')}")
            return claims
        except Exception as exc:
            # Normalize to InvalidTokenError for callers
            # Print for local debugging (do not leak in prod logs)
            print("jwt_utils: JWKS verification failed:", repr(exc))
            raise InvalidTokenError(str(exc)) from exc

    # Fallback: static PEM public key if provided
    if CLERK_PEM_PUBLIC_KEY:
        try:
            verify_kwargs = {"algorithms": ["RS256"]}
            if CLERK_AUD:
                verify_kwargs["audience"] = CLERK_AUD
            claims = jwt_decode(token, CLERK_PEM_PUBLIC_KEY, **verify_kwargs)
            if CLERK_ISS and claims.get("iss") != CLERK_ISS:
                raise InvalidTokenError(f"issuer mismatch: expected {CLERK_ISS}, got {claims.get('iss')}")
            return claims
        except Exception as exc:
            print("jwt_utils: PEM verification failed:", repr(exc))
            raise InvalidTokenError(str(exc)) from exc

    # If neither JWKS nor PEM provided, raise a clear error
    raise InvalidTokenError("No JWKS URL (CLERK_JWKS_URL/CLERK_JWKS_URI) or CLERK_PEM_PUBLIC_KEY configured")
