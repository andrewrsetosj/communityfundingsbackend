"""Ledger v2 — donor + creator views. Schema-tolerant."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db

router = APIRouter(prefix="/api/ledger-v2", tags=["ledger-v2"])


def _extract_user_id(authorization: Optional[str]) -> Optional[str]:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:]
    try:
        import jwt as pyjwt
        return pyjwt.decode(token, options={"verify_signature": False}).get("sub")
    except Exception:
        return None


@router.get("/donor")
async def donor_ledger(db: AsyncSession = Depends(get_db),
                       authorization: Optional[str] = Header(None)):
    uid = _extract_user_id(authorization)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        r = await db.execute(text("""
            SELECT d.donation_id, d.amount, d.status, d.time_created,
                   d.campaign_id,
                   COALESCE(d.donor_name, 'Anonymous') AS donor_name,
                   COALESCE(d.is_anonymous, FALSE)     AS is_anonymous,
                   COALESCE(d.message, '')             AS message,
                   c.title AS campaign_title,
                   c.url   AS campaign_slug,
                   COALESCE(cr.name || ' ' || cr.last_name, cr.name, '') AS campaign_creator_name
              FROM donations d
              JOIN campaigns c  ON c.campaign_id = d.campaign_id
         LEFT JOIN creators cr ON cr.creator_id = c.creator_id
             WHERE d.donor_creator_id = :uid
             ORDER BY d.time_created DESC
             LIMIT 200
        """), {"uid": uid})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ledger query failed: {e}")

    out, total = [], 0.0
    for row in r.mappings().all():
        amt = float(row["amount"] or 0)
        if row["status"] == "succeeded":
            total += amt
        out.append({
            "donation_id": row["donation_id"],
            "amount": amt,
            "status": row["status"],
            "created_at": row["time_created"].isoformat() if row["time_created"] else None,
            "campaign_id": row["campaign_id"],
            "campaign_title": row["campaign_title"],
            "campaign_slug": row["campaign_slug"],
            "campaign_creator_name": row["campaign_creator_name"],
            "is_anonymous": row["is_anonymous"],
            "message": row["message"],
        })
    return {"donations": out, "total_donated": round(total, 2), "count": len(out)}


@router.get("/creator")
async def creator_ledger(db: AsyncSession = Depends(get_db),
                         authorization: Optional[str] = Header(None)):
    uid = _extract_user_id(authorization)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        r = await db.execute(text("""
            SELECT d.donation_id, d.amount, d.status, d.time_created,
                   COALESCE(d.donor_name, 'Anonymous') AS donor_name,
                   COALESCE(d.is_anonymous, FALSE)     AS is_anonymous,
                   COALESCE(d.message, '')             AS message,
                   c.campaign_id, c.title AS campaign_title, c.url AS campaign_slug
              FROM donations d
              JOIN campaigns c ON c.campaign_id = d.campaign_id
             WHERE c.creator_id = :uid
             ORDER BY d.time_created DESC
             LIMIT 200
        """), {"uid": uid})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Creator ledger query failed: {e}")

    out, total = [], 0.0
    for row in r.mappings().all():
        amt = float(row["amount"] or 0)
        if row["status"] == "succeeded":
            total += amt
        out.append({
            "donation_id": row["donation_id"],
            "amount": amt,
            "status": row["status"],
            "created_at": row["time_created"].isoformat() if row["time_created"] else None,
            "donor_name": "Anonymous" if row["is_anonymous"] else row["donor_name"],
            "is_anonymous": row["is_anonymous"],
            "message": row["message"],
            "campaign_id": row["campaign_id"],
            "campaign_title": row["campaign_title"],
            "campaign_slug": row["campaign_slug"],
        })
    return {"donations": out, "total_raised": round(total, 2), "count": len(out)}


@router.get("/campaign/{campaign_id}/donors")
async def campaign_donors(campaign_id: int, db: AsyncSession = Depends(get_db)):
    r = await db.execute(text("""
        SELECT donation_id, amount, time_created,
               COALESCE(donor_name, 'Anonymous') AS donor_name,
               COALESCE(is_anonymous, FALSE)     AS is_anonymous,
               COALESCE(message, '')             AS message,
               status
          FROM donations
         WHERE campaign_id = :cid AND status = 'succeeded'
         ORDER BY time_created DESC
         LIMIT 100
    """), {"cid": campaign_id})
    return {"donors": [
        {"donation_id": row["donation_id"], "amount": float(row["amount"] or 0),
         "donor_name": "Anonymous" if row["is_anonymous"] else row["donor_name"],
         "is_anonymous": row["is_anonymous"], "message": row["message"],
         "created_at": row["time_created"].isoformat() if row["time_created"] else None}
        for row in r.mappings().all()
    ]}
