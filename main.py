import os
import asyncio
from fastapi import FastAPI, Header, HTTPException, Depends
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from jwt import InvalidTokenError
from db import get_pool, init_db
from jwt_utils import verify_token

load_dotenv()

PORT = int(os.getenv("PORT", 4000))

app = FastAPI(title="cf-backend (FastAPI)")

# Startup: initialize DB connection pool and ensure table exists
@app.on_event("startup")
async def startup():
    await init_db()

# Dependency: verify Authorization header and attach payload
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
    # payload is a dict with claims
    return payload

@app.post("/api/auth/verify-and-store")
async def verify_and_store(payload: dict = Depends(get_current_user)):
    clerk_user_id = payload.get("sub")
    if not clerk_user_id:
        raise HTTPException(status_code=400, detail="Token missing subject (sub)")
    pool = await get_pool()
    async with pool.acquire() as conn:
        # upsert: insert if not exists
        await conn.execute("""
            INSERT INTO clerk_users (clerk_id)
            VALUES ($1)
            ON CONFLICT (clerk_id) DO NOTHING
        """, clerk_user_id)
    return JSONResponse({"ok": True, "clerkUserId": clerk_user_id})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=True)
