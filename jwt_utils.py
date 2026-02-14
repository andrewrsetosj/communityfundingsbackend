import os
import time
import json
import requests
import jwt        # PyJWT
from jwt import PyJWKClient, InvalidTokenError
from dotenv import load_dotenv

load_dotenv()

CLERK_PEM_PUBLIC_KEY = os.getenv("CLERK_PEM_PUBLIC_KEY")  # optional
CLERK_JWKS_URI = os.getenv("CLERK_JWKS_URI")              # optional
JWT_EXPECTED_ISSUER = os.getenv("JWT_EXPECTED_ISSUER")
JWT_EXPECTED_AUDIENCE = os.getenv("JWT_EXPECTED_AUDIENCE")
JWKS_CACHE_TTL = int(os.getenv("JWKS_CACHE_TTL", "300"))

# Simple JWKS cache
_jwks_cache = {"keys": None, "fetched_at": 0}

def _fetch_jwks(jwks_uri):
    now = time.time()
    if _jwks_cache["keys"] and now - _jwks_cache["fetched_at"] < JWKS_CACHE_TTL:
        return _jwks_cache["keys"]
    resp = requests.get(jwks_uri, timeout=5)
    resp.raise_for_status()
    jwks = resp.json()
    _jwks_cache["keys"] = jwks
    _jwks_cache["fetched_at"] = now
    return jwks

def verify_token_with_pem(token: str):
    """Verify JWT using static PEM public key (RS256)."""
    if not CLERK_PEM_PUBLIC_KEY:
        raise ValueError("CLERK_PEM_PUBLIC_KEY not configured")
    options = {
        "verify_exp": True,
        "verify_aud": bool(JWT_EXPECTED_AUDIENCE),
    }
    return jwt.decode(
        token,
        CLERK_PEM_PUBLIC_KEY,
        algorithms=["RS256"],
        audience=JWT_EXPECTED_AUDIENCE or None,
        issuer=JWT_EXPECTED_ISSUER or None,
        options=options
    )

def verify_token_with_jwks(token: str):
    """Rotation-safe verification using JWKS. Uses PyJWKClient to fetch keys."""
    if not CLERK_JWKS_URI:
        raise ValueError("CLERK_JWKS_URI not configured")
    # Using PyJWKClient from PyJWT
    jwk_client = PyJWKClient(CLERK_JWKS_URI)
    signing_key = jwk_client.get_signing_key_from_jwt(token)
    key = signing_key.key
    options = {"verify_exp": True, "verify_aud": bool(JWT_EXPECTED_AUDIENCE)}
    return jwt.decode(
        token,
        key=key,
        algorithms=["RS256"],
        audience=JWT_EXPECTED_AUDIENCE or None,
        issuer=JWT_EXPECTED_ISSUER or None,
        options=options
    )

def verify_token(token: str):
    """Try JWKS first (recommended), fall back to PEM if configured."""
    last_err = None
    if CLERK_JWKS_URI:
        try:
            return verify_token_with_jwks(token)
        except Exception as e:
            last_err = e
            # continue to try PEM if available
    if CLERK_PEM_PUBLIC_KEY:
        try:
            return verify_token_with_pem(token)
        except Exception as e:
            last_err = e
    # If we get here, verification failed or no method configured
    raise InvalidTokenError(f"Token verification failed: {last_err}")
