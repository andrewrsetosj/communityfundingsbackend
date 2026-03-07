"""
Minimal backend: health + POST /api/campaigns/finalize.
No SQLAlchemy ORM, no table creation — uses db.py (asyncpg) to insert into your existing campaigns table.
"""

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

print("DATABASE_URL:", os.getenv("DATABASE_URL"))

import db


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await db.close_pool()


app = FastAPI(
    title="Community Fundings API",
    version="1.0.0",
    lifespan=lifespan,
)

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


@app.get("/")
async def root():
    return {
        "status": "healthy",
        "service": "Community Fundings API",
    }


@app.get("/api/config")
async def get_config():
    return {
        "stripe_publishable_key": os.getenv("STRIPE_PUBLISHABLE_KEY", ""),
        "platform_fee_percent": float(os.getenv("PLATFORM_FEE_PERCENT", "5.0")),
    }


@app.get("/api/campaigns/check-slug")
async def check_slug(slug: str):
    """
    Check if a vanity slug is available.
    Query param: slug
    Returns: {"available": true/false}
    """
    if not slug or not slug.strip():
        raise HTTPException(status_code=400, detail="Slug is required")
    try:
        available = await db.check_slug_available(slug)
        return {"available": available}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/campaigns/finalize")
async def finalize_campaign(data: dict):
    """
    Submit campaign draft from create-project payment page.
    Body = full draft JSON (creator_id, title, description_html, vanity_slug, etc.).
    Inserts into public.campaigns; returns { campaign_id, slug }.
    """
    try:
        result = await db.finalize_campaign(data)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        err = str(e).lower()
        if "does not exist" in err or "undefinedtable" in err:
            raise HTTPException(
                status_code=503,
                detail="Database table public.campaigns is missing. Run your DDL to create it.",
            )
        if "foreign key" in err or "foreignkey" in err:
            raise HTTPException(
                status_code=400,
                detail="creator_id must exist in public.creators(creator_id). Add the creator first.",
            )
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "4000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
