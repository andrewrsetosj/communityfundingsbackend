"""
Community Fundings — FastAPI Backend
Full crowdfunding platform with Stripe + PostgreSQL RDS
"""

import os
import traceback
import sys
from fastapi import FastAPI, Header, HTTPException, Depends
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from jwt import InvalidTokenError
from dotenv import load_dotenv
# from db import get_pool, init_db, upsert_creator
from jwt_utils import verify_token
from db import get_pool, init_db as asyncpg_init_db, upsert_creator
from app.database import init_db as sa_init_db, engine

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

from app.database import init_db, engine
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Starting Community Fundings API...")
    # initialize asyncpg tables (creators, clerk_users) — from your db.py
    await asyncpg_init_db()
    print("✅ asyncpg tables ready (creators/clerk_users)")

    # initialize SQLAlchemy tables (app.database) if needed by other parts
    await sa_init_db()
    print("✅ SQLAlchemy tables ready")

    yield
    await engine.dispose()
    print("👋 Shutdown complete")


app = FastAPI(
    title="Community Fundings API",
    description="Full crowdfunding platform — Stripe payments, Stripe Connect payouts, PostgreSQL RDS",
    version="3.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    # allow_origins=[
    #     os.getenv("FRONTEND_URL", "http://localhost:3000"),
    #     "http://127.0.0.1:3000",
    #     "http://localhost:5173",
    # ],
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:5173", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type", "Accept", "Origin", "*"],
)

# ── Mount all routers ──────────────────────────────────────────────────────
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
    """Public config the frontend needs (no secrets!)."""
    return {
        "stripe_publishable_key": os.getenv("STRIPE_PUBLISHABLE_KEY", ""),
        "platform_fee_percent": float(os.getenv("PLATFORM_FEE_PERCENT", "5.0")),
    }

@app.post("/api/auth/verify-and-store")
async def verify_and_store(request: Request):
    auth = request.headers.get("authorization")
    print("DEBUG: Authorization header received:", repr(auth))
    try:
        body_bytes = await request.body()
        print("DEBUG: Request body (bytes):", body_bytes)
    except Exception as e:
        print("DEBUG: Failed to read body:", e)

    # We'll initialise clerk_user_id to None and only proceed to DB if it's set
    clerk_user_id = None

    try:
        if not auth:
            raise HTTPException(status_code=401, detail="No Authorization header received")

        if not auth.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="Bad bearer format")

        token = auth.split(" ", 1)[1].strip()
        print("DEBUG: Token preview:", token[:30], "...")

        # Verify token and extract claims. This may raise InvalidTokenError or other errors.
        try:
            from jwt_utils import verify_token  # safe local import
            claims = verify_token(token)
            print("DEBUG: JWT claims:", claims)
        except InvalidTokenError as ive:
            print("DEBUG: Invalid token:", ive)
            raise HTTPException(status_code=401, detail=f"Invalid token: {ive}")
        except Exception as ve:
            print("ERROR: Token verification error:", ve, file=sys.stderr)
            traceback.print_exc()
            raise HTTPException(status_code=401, detail=f"Token verification error: {ve}")

        # Extract user id from claims
        clerk_user_id = claims.get("sub")
        if not clerk_user_id:
            raise HTTPException(status_code=400, detail="Token missing subject (sub)")
        print("DEBUG: clerk_user_id to upsert:", clerk_user_id)

        # --- DB UPSERT: only run if clerk_user_id is set ---
        try:
            ret = await upsert_creator(creator_id=clerk_user_id)
            print("DEBUG: upsert_creator returned:", ret)
        except Exception as db_err:
            print("ERROR: DB upsert threw exception:", db_err, file=sys.stderr)
            traceback.print_exc()
            raise HTTPException(status_code=500, detail="DB upsert error")

        # --- Verification SELECT to confirm write (logs only) ---
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow("SELECT * FROM creators WHERE creator_id = $1", clerk_user_id)
                print("DEBUG: row after upsert:", row)
        except Exception as verify_err:
            print("WARNING: verification SELECT failed:", verify_err, file=sys.stderr)
            traceback.print_exc()

        return JSONResponse({"ok": True, "creatorId": clerk_user_id})

    except HTTPException:
        # re-raise FastAPI HTTPExceptions so FastAPI will return the proper status
        raise
    except Exception as e:
        # catch-all and log full traceback
        print("ERROR: Exception in /api/auth/verify-and-store:", file=sys.stderr)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")



@app.post("/some/protected")
async def protected_handler(payload: dict = Depends(get_current_user)):
    # payload contains JWT claims; use payload['sub'] as user id
    user_id = payload['sub']
    ...

@app.get("/health/db")
async def health_check_db():
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("SELECT 1;")
        return {"status": "ok", "db": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")



if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

    


@app.post("/debug/echo-authorization")
async def debug_echo_authorization(request: Request):
    # WARNING: dev-only. Do NOT keep in prod.
    headers = dict(request.headers)
    return {"headers": headers}
