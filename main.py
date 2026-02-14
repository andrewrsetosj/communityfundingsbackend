"""
Community Fundings — FastAPI Backend
Full crowdfunding platform with Stripe + PostgreSQL RDS
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import os
from dotenv import load_dotenv

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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)