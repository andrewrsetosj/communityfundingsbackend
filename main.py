"""
Minimal backend: asyncpg-only campaign endpoints.
No SQLAlchemy startup path (avoids psycopg2 dependency issues).
"""

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

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
    return {"status": "healthy", "service": "Community Fundings API"}


@app.get("/api/config")
async def get_config():
    return {
        "stripe_publishable_key": os.getenv("STRIPE_PUBLISHABLE_KEY", ""),
        "platform_fee_percent": float(os.getenv("PLATFORM_FEE_PERCENT", "5.0")),
    }


@app.get("/api/campaigns")
async def get_campaigns(status: str | None = None, sort: str = "recent", per_page: int = 12):
    try:
        return await db.list_campaigns(status=status, sort=sort, per_page=per_page)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/campaigns/check-slug")
async def check_slug(slug: str):
    if not slug or not slug.strip():
        raise HTTPException(status_code=400, detail="Slug is required")
    try:
        return {"available": await db.check_slug_available(slug)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/campaigns/finalize")
async def finalize_campaign(data: dict):
    try:
        return await db.finalize_campaign(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        err = str(e).lower()
        if "relation \"public.campaigns\" does not exist" in err or "relation public.campaigns does not exist" in err:
            raise HTTPException(
                status_code=503,
                detail="Database table public.campaigns is missing. Run your DDL to create it.",
            )
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/auth/verify-and-store")
async def verify_and_store_user(request: Request):
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    body = await request.json()
    user_data = body.get("user", {})
    creator_id = user_data.get("id")
    if not creator_id:
        raise HTTPException(status_code=400, detail="Missing user.id")

    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT creator_id FROM public.creators WHERE creator_id = $1",
            creator_id,
        )
        if row:
            return {"status": "already_exists", "creator_id": creator_id}

        await conn.execute(
            """
            INSERT INTO public.creators (creator_id, user_type, name, last_name, email, time_creation)
            VALUES ($1, $2, $3, $4, $5, NOW())
            """,
            creator_id,
            1,
            user_data.get("first_name") or "",
            user_data.get("last_name") or "",
            user_data.get("email") or None,
        )

    return {"status": "created", "creator_id": creator_id}


if __name__ == "__main__":
    import uvicorn

    raw_port = os.getenv("PORT", "4000")
    digits = "".join(ch for ch in raw_port if ch.isdigit()) or "4000"
    uvicorn.run("main:app", host="0.0.0.0", port=int(digits), reload=True)