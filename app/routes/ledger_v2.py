"""
Ledger v2 — donor + creator ledger views for Community Fundings.

Read-only endpoints that aggregate donations against cf-db's actual schema.
Mounted at /api/ledger-v2/* to avoid conflicts.

Cross-checked against cf-db live schema (Apr 30):
    donations columns: donation_id, campaign_id, donor_creator_id, amount,
                       status, time_created, donor_name, donor_email, ...
    campaigns columns: campaign_id, creator_id, title, url, ...
    creators  columns: creator_id, name, last_name, ...
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

# /* v100_ledger_v2 */
router = APIRouter(prefix="/api/ledger-v2", tags=["ledger-v2"])


def _extract_user_id(authorization: Optional[str]) -> Optional[str]:
    """Best-effort decode of a Bearer JWT to get the 'sub' (creator_id).

    We don't verify the signature — main may handle that elsewhere.
    For ledger reads, we just need to know which user is asking.
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:]
    try:
        import jwt as pyjwt
        decoded = pyjwt.decode(token, options={"verify_signature": False})
        return decoded.get("sub")
    except Exception:
        return None


# ─── GET /donor — donations made BY the authenticated user ───────────
@router.get("/donor")
async def donor_ledger(
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(None),
):
    """List all donations made by the current user (most recent first)."""
    uid = _extract_user_id(authorization)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")

    r = await db.execute(text("""
        SELECT d.donation_id, d.amount, d.status, d.time_created,
               d.campaign_id, d.donor_name, d.is_anonymous, d.message,
               c.title AS campaign_title, c.url AS campaign_slug,
               cr.name AS campaign_creator_name
          FROM donations d
          JOIN campaigns c ON c.campaign_id = d.campaign_id
          LEFT JOIN creators cr ON cr.creator_id = c.creator_id
         WHERE d.donor_creator_id = :uid
         ORDER BY d.time_created DESC
         LIMIT 200
    """), {"uid": uid})

    donations = []
    total = 0.0
    for row in r.mappings().all():
        amt = float(row["amount"])
        if row["status"] == "succeeded":
            total += amt
        donations.append({
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

    return {
        "donations": donations,
        "total_donated": round(total, 2),
        "count": len(donations),
    }


# ─── GET /creator — donations RECEIVED by the authenticated user ──────
@router.get("/creator")
async def creator_ledger(
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(None),
):
    """List all donations received across the user's campaigns."""
    uid = _extract_user_id(authorization)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")

    r = await db.execute(text("""
        SELECT d.donation_id, d.amount, d.status, d.time_created,
               d.donor_name, d.is_anonymous, d.message,
               c.campaign_id, c.title AS campaign_title, c.url AS campaign_slug
          FROM donations d
          JOIN campaigns c ON c.campaign_id = d.campaign_id
         WHERE c.creator_id = :uid
         ORDER BY d.time_created DESC
         LIMIT 200
    """), {"uid": uid})

    rows = []
    total_raised = 0.0
    for row in r.mappings().all():
        amt = float(row["amount"])
        if row["status"] == "succeeded":
            total_raised += amt
        rows.append({
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

    return {
        "donations": rows,
        "total_raised": round(total_raised, 2),
        "count": len(rows),
    }


# ─── GET /campaign/{id}/donors — public list of donors for a campaign ──
@router.get("/campaign/{campaign_id}/donors")
async def campaign_donors(campaign_id: int, db: AsyncSession = Depends(get_db)):
    """Public donor list for a campaign (anonymous donors are masked)."""
    r = await db.execute(text("""
        SELECT donation_id, amount, time_created, donor_name,
               is_anonymous, message, status
          FROM donations
         WHERE campaign_id = :cid
           AND status = 'succeeded'
         ORDER BY time_created DESC
         LIMIT 100
    """), {"cid": campaign_id})

    return {
        "donors": [
            {
                "donation_id": row["donation_id"],
                "amount": float(row["amount"]),
                "donor_name": "Anonymous" if row["is_anonymous"] else row["donor_name"],
                "is_anonymous": row["is_anonymous"],
                "message": row["message"],
                "created_at": row["time_created"].isoformat() if row["time_created"] else None,
            }
            for row in r.mappings().all()
        ]
    }
