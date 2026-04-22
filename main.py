"""
Community Fundings — FastAPI Backend
Full crowdfunding platform with Stripe + PostgreSQL RDS
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Always load backend/.env next to this file — not "whatever .env is in the shell cwd"
_backend_dir = Path(__file__).resolve().parent
load_dotenv(_backend_dir / ".env")
# Exported vars in your shell win over .env unless you: unset them, or set DOTENV_OVERRIDE=1
if os.getenv("DOTENV_OVERRIDE", "").lower() in ("1", "true", "yes"):
    load_dotenv(_backend_dir / ".env", override=True)
import sys
import traceback
from contextlib import asynccontextmanager
from app.routes.campaign_page import router as campaign_page_router
from fastapi import FastAPI, Header, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from jwt import InvalidTokenError
from app.routes.follows import router as follows_router
from app.routes.saved_campaigns import router as saved_campaigns_router
from app.routes.notifications import router as notifications_router
from app.routes.misc_reports import router as misc_reports_router

from jwt_utils import verify_token
from app.db import (
    get_pool,
    init_db as asyncpg_init_db,
    upsert_creator,
    close_pool,
)

# Routers
from app.routes.auth import router as auth_router
from app.routes.campaigns import router as campaigns_router
from app.routes.payments import router as payments_router
from app.routes.refunds import router as refunds_router
from app.routes.users import router as users_router
from app.routes.updates import router as updates_router
from app.routes.comments import router as comments_router
from app.routes.reports import router as reports_router
from app.routes.uploads import router as uploads_router
from app.routes.admin import router as admin_router
from app.routes.profile_page import router as profile_page_router
from app.routes.locations import router as locations_router




# ─────────────────────────────────────────────────────────────────────────────
# Lifespan (startup / shutdown)
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Starting Community Fundings API...")

    # Only initialize asyncpg schema (creators table)
    await asyncpg_init_db()
    print("✅ asyncpg creators table ready")

    yield

    # Shutdown cleanup
    try:
        await close_pool()
        print("👋 AsyncPG pool closed")
    except Exception as e:
        print("WARN: Error closing pool:", e)


app = FastAPI(
    title="Community Fundings API",
    description="Full crowdfunding platform — Stripe payments, Stripe Connect payouts, PostgreSQL RDS",
    version="3.0.0",
    lifespan=lifespan,
)


# ─────────────────────────────────────────────────────────────────────────────
# CORS
# ─────────────────────────────────────────────────────────────────────────────

def _cors_allow_origins() -> list[str]:
    """Local dev defaults + FRONTEND_URL + optional comma-separated CORS_ORIGINS."""
    origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    for raw in (
        os.getenv("FRONTEND_URL", ""),
        *(os.getenv("CORS_ORIGINS", "").split(",")),
    ):
        o = raw.strip().rstrip("/")
        if o and o not in origins:
            origins.append(o)
    return origins


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────────────────
# Routers
# ─────────────────────────────────────────────────────────────────────────────

app.include_router(auth_router)
app.include_router(campaigns_router)
app.include_router(payments_router)
app.include_router(refunds_router)
app.include_router(users_router)
app.include_router(updates_router)
app.include_router(comments_router)
app.include_router(reports_router)
app.include_router(uploads_router)
app.include_router(admin_router)
app.include_router(campaign_page_router)
app.include_router(profile_page_router)
app.include_router(follows_router)
app.include_router(saved_campaigns_router)
app.include_router(notifications_router)
app.include_router(locations_router)
app.include_router(misc_reports_router)

# ─────────────────────────────────────────────────────────────────────────────
# Auth Dependency
# ─────────────────────────────────────────────────────────────────────────────

async def get_current_user(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Authorization must be Bearer token")

    token = authorization.split(" ", 1)[1].strip()

    try:
        payload = verify_token(token)
    except InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Token verification error: {str(e)}")

    return payload


# ─────────────────────────────────────────────────────────────────────────────
# Root / Config
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/test-profile-route")
async def test_profile_route():
    return {"ok": True}

@app.get("/")
async def root():
    return {
        "status": "healthy",
        "service": "Community Fundings API v3",
        "database": "PostgreSQL RDS",
        "payments": "Stripe + Stripe Connect",
    }


@app.get("/api/config")
async def get_config():
    return {
        "stripe_publishable_key": os.getenv("STRIPE_PUBLISHABLE_KEY", ""),
        "platform_fee_percent": float(os.getenv("PLATFORM_FEE_PERCENT", "5.0")),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Clerk JWT Verify + Store Creator
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/auth/verify-and-store")
async def verify_and_store(request: Request):
    auth = request.headers.get("authorization")
    if not auth:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Bad Authorization header format")
    token = auth.split(" ", 1)[1].strip()

    # verify JWT and get claims
    try:
        claims = verify_token(token)
    except InvalidTokenError as it:
        raise HTTPException(status_code=401, detail=f"Invalid token: {it}")

    clerk_id = claims.get("sub") or claims.get("id")
    if not clerk_id:
        raise HTTPException(status_code=400, detail="Token does not contain subject (sub)")

    # Optionally extract first/last/email from claims, else fallback to JSON body
    first_name = claims.get("first_name") or claims.get("given_name")
    last_name = claims.get("last_name") or claims.get("family_name")
    email = claims.get("email") or claims.get("primary_email_address")

    try:
        payload = await request.json()
        body_user = payload.get("user") if isinstance(payload, dict) else None
    except Exception:
        body_user = None

    if body_user:
        first_name = first_name or body_user.get("first_name") or body_user.get("firstName")
        last_name = last_name or body_user.get("last_name") or body_user.get("lastName")
        email = email or body_user.get("email") or body_user.get("primaryEmail")

    # Upsert into DB (only passes clerk id + optional fields)
    try:
        row = await upsert_creator(
            creator_id=str(clerk_id),
            name=first_name,
            last_name=last_name,
            email=email,
        )
    except Exception as db_err:
        print("ERROR: DB upsert failed", file=sys.stderr)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="DB upsert error")

    # helper to convert asyncpg.Record -> JSON-serializable dict
    def serialize_record(rec):
        if not rec:
            return None
        d = dict(rec)
        for k, v in list(d.items()):
            if v is None:
                continue
            # datetime -> ISO string
            try:
                import datetime as _dt, decimal as _dec
                if isinstance(v, _dt.datetime) or isinstance(v, _dt.date):
                    d[k] = v.isoformat()
                elif isinstance(v, _dec.Decimal):
                    # convert decimals to float or string (choose string to preserve precision)
                    d[k] = float(v)
                # optionally handle bytes etc here if needed
            except Exception:
                # fallback to str()
                d[k] = str(v)
        return d

    # optional: fetch stored row for debug and return
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            stored = await conn.fetchrow(
                "SELECT creator_id, first_name, last_name, email, time_creation, is_business FROM creators WHERE creator_id = $1",
                str(clerk_id),
            )
            print("DEBUG: stored row after upsert:", dict(stored) if stored else None)
    except Exception:
        traceback.print_exc()

    return JSONResponse({
        "ok": True,
        "creatorId": str(clerk_id),
        "dbRow": serialize_record(row)
    })

# ─────────────────────────────────────────────────────────────────────────────
# Health Check
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health/db")
async def health_check_db():
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("SELECT 1;")
        return {"status": "ok", "db": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


# ─────────────────────────────────────────────────────────────────────────────
# Dev Debug Endpoint (remove in prod)
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/debug/echo-authorization")
async def debug_echo_authorization(request: Request):
    return {"headers": dict(request.headers)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=4000, reload=True)