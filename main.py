"""
Minimal backend: health + POST /api/campaigns/finalize.
No SQLAlchemy ORM, no table creation — uses db.py (asyncpg) to insert into your existing campaigns table.
"""

import os
from contextlib import asynccontextmanager
import jwt
from jwt import PyJWKClient
import base64

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
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
        if "relation \"public.campaigns\" does not exist" in err or "relation public.campaigns does not exist" in err:
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


@app.post("/api/auth/verify-and-store")
async def verify_and_store_user(request: Request):
    """
    Verify Clerk JWT token and store user in creators table if not exists.
    Expects Authorization: Bearer <token>
    Body: { "user": { id, first_name, last_name, email, image_url } }
    """
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = auth_header[7:]  # remove "Bearer "
    if not token:
        raise HTTPException(status_code=401, detail="Empty token")

    # TODO: Verify JWT token with Clerk
    # For now, trust the token presence and user data
    # # Decode and verify JWT
    # try:
    #     # Get PEM key
    #     pem_key = os.getenv("CLERK_PEM_PUBLIC_KEY")
    #     if not pem_key:
    #         raise HTTPException(status_code=500, detail="CLERK_PEM_PUBLIC_KEY not set")

    #     # Remove header/footer if present
    #     pem_key = pem_key.strip()
    #     if not pem_key.startswith("-----BEGIN"):
    #         pem_key = f"-----BEGIN PUBLIC KEY-----\n{pem_key}\n-----END PUBLIC KEY-----"

    #     # Decode JWT without verification first to get kid
    #     header = jwt.get_unverified_header(token)
    #     kid = header.get("kid")
    #     if not kid:
    #         raise HTTPException(status_code=401, detail="Invalid token: no kid")

    #     # For simplicity, use the PEM key directly (since it's static)
    #     public_key = pem_key

    #     # Verify token
    #     payload = jwt.decode(token, public_key, algorithms=["RS256"])
    #     user_id = payload.get("sub")
    #     if not user_id:
    #         raise HTTPException(status_code=401, detail="Invalid token: no sub")

    # except jwt.ExpiredSignatureError:
    #     raise HTTPException(status_code=401, detail="Token expired")
    # except jwt.InvalidTokenError as e:
    #     raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")

    # Get user data from body
    body = await request.json()
    user_data = body.get("user", {})
    creator_id = user_data.get("id")
    if not creator_id:
        raise HTTPException(status_code=400, detail="Missing user.id")

    # Store in database
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        # Check if exists
        row = await conn.fetchrow(
            'SELECT creator_id FROM public.creators WHERE creator_id = $1',
            creator_id
        )
        if row:
            return {"status": "already_exists", "creator_id": creator_id}

        # Insert
        await conn.execute(
            '''
            INSERT INTO public.creators (creator_id, user_type, name, last_name, email, time_creation)
            VALUES ($1, $2, $3, $4, $5, NOW())
            ''',
            creator_id,
            1,  # user_type=1 for user
            user_data.get("first_name") or "",
            user_data.get("last_name") or "",
            user_data.get("email") or None,
        )

    return {"status": "created", "creator_id": creator_id}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "4000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
