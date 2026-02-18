# jwt_utils.py
import os
import time
from dotenv import load_dotenv
import requests
import jwt
from jwt import PyJWKClient, InvalidTokenError, decode as jwt_decode

load_dotenv()

CLERK_PEM_PUBLIC_KEY = os.getenv("CLERK_PEM_PUBLIC_KEY")
CLERK_JWKS_URI = os.getenv("CLERK_JWKS_URI")
# implement later
# JWT_EXPECTED_ISSUER = os.getenv("JWT_EXPECTED_ISSUER")
# JWT_EXPECTED_AUDIENCE = os.getenv("JWT_EXPECTED_AUDIENCE")
JWKS_CACHE_TTL = int(os.getenv("JWKS_CACHE_TTL", "300"))

# Simple JWKS cache (fallback if you don't use PyJWKClient repeatedly)
_jwks_cache = {"fetched_at": 0, "jwks": None}

def _get_jwk_client(jwks_uri):
    return PyJWKClient(jwks_uri)

def verify_token_with_jwks(token: str):
    if not CLERK_JWKS_URI:
        raise ValueError("CLERK_JWKS_URI not configured")
    # auto caching and rotation w PyJWTClient
    jwk_client = _get_jwk_client(CLERK_JWKS_URI)
    signing_key = jwk_client.get_signing_key_from_jwt(token)
    key = signing_key.key
    # verify with extracted key
    return jwt.decode(
        token,
        key=key,
        algorithms=["RS256"],
        # issuer=JWT_EXPECTED_ISSUER or None,
        # audience=JWT_EXPECTED_AUDIENCE or None,
    )
    return payload

def verify_token_with_pem(token: str):
    if not CLERK_PEM_PUBLIC_KEY:
        raise ValueError("CLERK_PEM_PUBLIC_KEY not configured")
    return jwt.decode(
        token,
        CLERK_PEM_PUBLIC_KEY,
        algorithms=["RS256"],
        # issuer=JWT_EXPECTED_ISSUER or None,
        # audience=JWT_EXPECTED_AUDIENCE or None,
    )
    return payload

def verify_token(token: str):
    last_err = None
    # Prefer JWKS (rotation-safe)
    if CLERK_JWKS_URI:
        try:
            return verify_token_with_jwks(token)
        except Exception as e:
            last_err = e
    # Fallback to static PEM if available
    if CLERK_PEM_PUBLIC_KEY:
        try:
            return verify_token_with_pem(token)
        except Exception as e:
            last_err = e
    raise InvalidTokenError(f"Token verification failed: {last_err}")
