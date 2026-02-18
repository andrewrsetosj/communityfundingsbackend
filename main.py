"""
Community Fundings — FastAPI Backend
Full crowdfunding platform with Stripe + PostgreSQL RDS
"""

import os
from fastapi import FastAPI, Header, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from jwt import InvalidTokenError
from dotenv import load_dotenv
from db import get_pool, init_db, upsert_clerk_user
from jwt_utils import verify_token


load_dotenv()
          

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
    await init_db()
    print("✅ Database tables ready")
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
    allow_origins=[
        os.getenv("FRONTEND_URL", "http://localhost:3000"),
        "http://127.0.0.1:3000",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
async def verify_and_store(payload: dict = Depends(get_current_user)):
    clerk_user_id = payload.get("sub")
    if not clerk_user_id:
        raise HTTPException(status_code=400, detail="Token missing subject (sub)")
    # optional: store the clerk user id into DB
    try:
        await upsert_clerk_user(clerk_user_id)  # adapt to your DB layer
    except Exception as e:
        # if you don't want failure here to block, log and continue
        print("DB upsert error:", e)
    return JSONResponse({"ok": True, "clerkUserId": clerk_user_id})

@app.post("/some/protected")
async def protected_handler(payload: dict = Depends(get_current_user)):
    # payload contains JWT claims; use payload['sub'] as user id
    user_id = payload['sub']
    ...



if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
