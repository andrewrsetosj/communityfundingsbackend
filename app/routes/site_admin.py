"""
Site Admin routes — production-grade platform moderation.
Separate from per-campaign Business Admin.

Endpoints:
  POST /api/site-admin/register            — create admin account (8–10 char code)
  POST /api/site-admin/login               — authenticate with code + name
  GET  /api/site-admin/dashboard           — overview: stats, growth, chart, top campaigns, recent donations
  GET  /api/site-admin/campaigns           — all campaigns, searchable + filterable
  POST /api/site-admin/campaigns/{id}/delete     — archive + remove
  POST /api/site-admin/campaigns/{id}/suspend    — mark suspended
  POST /api/site-admin/campaigns/{id}/reinstate  — mark active
  GET  /api/site-admin/users               — all creators, searchable + filterable
  POST /api/site-admin/users/{cid}/block
  POST /api/site-admin/users/{cid}/unblock
  GET  /api/site-admin/reports             — moderation queue (campaigns + comments)
  POST /api/site-admin/comments/{id}/delete
  GET  /api/site-admin/transactions        — donation stream, filterable
  GET  /api/site-admin/activity            — admin audit log
"""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from pydantic import BaseModel
from app.database import get_db

router = APIRouter(prefix="/api/site-admin", tags=["site-admin"])


# ─── Models ────────────────────────────────────────────────────────────────

class AdminCredentials(BaseModel):
    access_code: str
    first_name: str
    last_name: str


# ─── Helpers ───────────────────────────────────────────────────────────────

async def _verify_admin(admin_id: int, db: AsyncSession):
    r = await db.execute(
        text("SELECT admin_id, first_name, last_name FROM site_admins WHERE admin_id = :id AND is_active = true"),
        {"id": admin_id},
    )
    row = r.mappings().first()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid or inactive admin session")
    return row


# Cached once per process after first check — avoids running to_regclass every action
_activity_log_exists: bool | None = None


async def _log(db: AsyncSession, admin_id: int, action: str, target_type: str | None = None,
               target_id: str | None = None, details: str | None = None):
    """
    Audit-log a mutating admin action.

    CRITICAL: This MUST never break the outer transaction.
    Uses a savepoint so INSERT failure doesn't abort the caller's commit,
    and short-circuits if the admin_activity_log table hasn't been provisioned.
    """
    global _activity_log_exists
    try:
        if _activity_log_exists is None:
            chk = await db.execute(text("SELECT to_regclass('public.admin_activity_log')"))
            _activity_log_exists = chk.scalar() is not None
        if not _activity_log_exists:
            return  # table missing — silently skip logging
        # Savepoint: if the INSERT fails for any reason, only this nested block rolls back
        async with db.begin_nested():
            await db.execute(text("""
                INSERT INTO admin_activity_log (admin_id, action, target_type, target_id, details)
                VALUES (:aid, :a, :tt, :ti, :d)
            """), {"aid": admin_id, "a": action, "tt": target_type, "ti": target_id, "d": details})
    except Exception:
        # Logging failures must NEVER cascade — the core admin action has already succeeded
        pass


def _pct(current: float, previous: float) -> float:
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return round(((current - previous) / previous) * 100, 1)


# ─── Authentication ────────────────────────────────────────────────────────

@router.post("/register")
async def register_admin(data: AdminCredentials, db: AsyncSession = Depends(get_db)):
    if len(data.access_code) < 8 or len(data.access_code) > 10:
        raise HTTPException(status_code=400, detail="Access code must be 8–10 characters")
    existing = await db.execute(
        text("SELECT admin_id FROM site_admins WHERE access_code = :c"),
        {"c": data.access_code},
    )
    if existing.first():
        raise HTTPException(status_code=409, detail="Access code already in use")
    await db.execute(
        text("INSERT INTO site_admins (access_code, first_name, last_name) VALUES (:c, :fn, :ln)"),
        {"c": data.access_code, "fn": data.first_name, "ln": data.last_name},
    )
    await db.commit()
    return {"status": "registered", "message": f"Admin {data.first_name} {data.last_name} registered"}


@router.post("/login")
async def login_admin(data: AdminCredentials, db: AsyncSession = Depends(get_db)):
    r = await db.execute(
        text("SELECT admin_id, first_name, last_name, is_active FROM site_admins WHERE access_code = :c"),
        {"c": data.access_code},
    )
    row = r.mappings().first()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid access code")
    if not row["is_active"]:
        raise HTTPException(status_code=403, detail="Admin account deactivated")
    if row["first_name"].lower() != data.first_name.lower() or row["last_name"].lower() != data.last_name.lower():
        raise HTTPException(status_code=401, detail="Name does not match access code")

    # Best-effort last_login stamp — column may not exist on legacy-provisioned tables.
    # Isolate in a savepoint so a missing column doesn't abort the login transaction.
    try:
        col = await db.execute(text("""
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'site_admins' AND column_name = 'last_login'
        """))
        if col.first():
            async with db.begin_nested():
                await db.execute(
                    text("UPDATE site_admins SET last_login = NOW() WHERE admin_id = :id"),
                    {"id": row["admin_id"]},
                )
    except Exception:
        pass  # never block authentication on bookkeeping

    await db.commit()
    return {
        "status": "authenticated",
        "admin_id": row["admin_id"],
        "name": f"{row['first_name']} {row['last_name']}",
    }


# ─── Overview Dashboard ────────────────────────────────────────────────────

@router.get("/dashboard")
async def dashboard(admin_id: int, db: AsyncSession = Depends(get_db)):
    await _verify_admin(admin_id, db)

    stats = await db.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM creators)                                         AS total_users,
            (SELECT COUNT(*) FROM campaigns)                                        AS total_campaigns,
            (SELECT COUNT(*) FROM campaigns WHERE status = 'active')                AS active_campaigns,
            (SELECT COUNT(*) FROM reports)                                          AS total_reports,
            (SELECT COUNT(*) FROM blocked_users)                                    AS total_blocked,
            (SELECT COUNT(*) FROM donations WHERE status = 'succeeded')             AS total_donations,
            (SELECT COALESCE(SUM(amount),0) FROM donations WHERE status='succeeded') AS total_raised,
            (SELECT COUNT(*) FROM comments)                                         AS total_comments
    """))
    s = stats.mappings().first()

    week = await db.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM creators  WHERE time_creation >= NOW() - INTERVAL '7 days')                                                AS u_this,
            (SELECT COUNT(*) FROM creators  WHERE time_creation >= NOW() - INTERVAL '14 days' AND time_creation < NOW() - INTERVAL '7 days')  AS u_prev,
            (SELECT COUNT(*) FROM campaigns WHERE time_created >= NOW() - INTERVAL '7 days')                                                AS c_this,
            (SELECT COUNT(*) FROM campaigns WHERE time_created >= NOW() - INTERVAL '14 days' AND time_created < NOW() - INTERVAL '7 days')  AS c_prev,
            (SELECT COALESCE(SUM(amount),0) FROM donations WHERE status='succeeded' AND time_created >= NOW() - INTERVAL '7 days')           AS r_this,
            (SELECT COALESCE(SUM(amount),0) FROM donations WHERE status='succeeded' AND time_created >= NOW() - INTERVAL '14 days' AND time_created < NOW() - INTERVAL '7 days') AS r_prev
    """))
    w = week.mappings().first()

    daily = await db.execute(text("""
        SELECT DATE(time_created) AS day,
               COALESCE(SUM(amount),0) AS amount,
               COUNT(*) AS count
        FROM donations
        WHERE status = 'succeeded' AND time_created >= NOW() - INTERVAL '14 days'
        GROUP BY DATE(time_created)
        ORDER BY day ASC
    """))
    chart = [{"date": str(r["day"]), "amount": float(r["amount"]), "count": r["count"]}
             for r in daily.mappings()]

    top = await db.execute(text("""
        SELECT c.campaign_id, c.title, c.creator_id, cr.name AS creator_name,
               c.amount_raised_cents, c.funding_goal_cents, c.backers, c.status
        FROM campaigns c
        LEFT JOIN creators cr ON cr.creator_id = c.creator_id
        WHERE c.status = 'active'
        ORDER BY c.amount_raised_cents DESC NULLS LAST
        LIMIT 5
    """))
    top_campaigns = [{
        "id": r["campaign_id"], "title": r["title"],
        "creator_name": r["creator_name"] or "Unknown",
        "raised": (r["amount_raised_cents"] or 0) / 100,
        "goal": (r["funding_goal_cents"] or 0) / 100,
        "backers": r["backers"] or 0, "status": r["status"],
    } for r in top.mappings()]

    recent = await db.execute(text("""
        SELECT d.donation_id, d.amount, d.time_created, d.status,
               d.donor_email, d.is_anonymous,
               c.title AS campaign_title, c.campaign_id
        FROM donations d
        LEFT JOIN campaigns c ON c.campaign_id = d.campaign_id
        WHERE d.status = 'succeeded'
        ORDER BY d.time_created DESC
        LIMIT 8
    """))
    recent_donations = [{
        "id": r["donation_id"], "amount": float(r["amount"]),
        "time": str(r["time_created"]),
        "donor": "Anonymous" if r["is_anonymous"] else (r["donor_email"] or "Unknown"),
        "campaign_title": r["campaign_title"] or "Unknown",
        "campaign_id": r["campaign_id"],
    } for r in recent.mappings()]

    alerts = await db.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM reports WHERE time_created >= NOW() - INTERVAL '24 hours') AS new_reports,
            (SELECT COUNT(*) FROM campaigns WHERE status = 'pending_review')                AS pending_campaigns
    """))
    a = alerts.mappings().first()

    return {
        "stats": {
            "total_users":       s["total_users"],
            "total_campaigns":   s["total_campaigns"],
            "active_campaigns":  s["active_campaigns"],
            "total_reports":     s["total_reports"],
            "total_blocked":     s["total_blocked"],
            "total_donations":   s["total_donations"],
            "total_raised":      float(s["total_raised"]),
            "total_comments":    s["total_comments"],
        },
        "growth": {
            "users_pct":         _pct(w["u_this"], w["u_prev"]),
            "campaigns_pct":     _pct(w["c_this"], w["c_prev"]),
            "raised_pct":        _pct(float(w["r_this"]), float(w["r_prev"])),
            "users_this_week":   w["u_this"],
            "campaigns_this_week": w["c_this"],
            "raised_this_week":  float(w["r_this"]),
        },
        "chart": chart,
        "top_campaigns": top_campaigns,
        "recent_donations": recent_donations,
        "alerts": {
            "new_reports_24h":   a["new_reports"] or 0,
            "pending_campaigns": a["pending_campaigns"] or 0,
        },
    }


# ─── Campaigns ────────────────────────────────────────────────────────────

@router.get("/campaigns")
async def list_campaigns(admin_id: int, search: str = "", status: str = "all",
                         limit: int = 50, offset: int = 0,
                         db: AsyncSession = Depends(get_db)):
    await _verify_admin(admin_id, db)
    clauses, params = [], {"limit": limit, "offset": offset}
    if search:
        clauses.append("(LOWER(c.title) LIKE LOWER(:s) OR LOWER(COALESCE(cr.name,'')) LIKE LOWER(:s))")
        params["s"] = f"%{search}%"
    if status != "all":
        clauses.append("c.status = :st")
        params["st"] = status
    where = "WHERE " + " AND ".join(clauses) if clauses else ""

    r = await db.execute(text(f"""
        SELECT c.campaign_id, c.title, c.status, c.creator_id, cr.name AS creator_name,
               c.funding_goal_cents, c.amount_raised_cents, c.backers, c.time_created,
               c.category, c.location,
               (SELECT COUNT(*) FROM reports r WHERE CAST(r.campaign_id AS TEXT) = CAST(c.campaign_id AS TEXT)) AS report_count
        FROM campaigns c
        LEFT JOIN creators cr ON cr.creator_id = c.creator_id
        {where}
        ORDER BY c.time_created DESC
        LIMIT :limit OFFSET :offset
    """), params)
    rows = r.mappings().all()

    cp = {k: v for k, v in params.items() if k not in ("limit", "offset")}
    total = (await db.execute(text(
        f"SELECT COUNT(*) FROM campaigns c LEFT JOIN creators cr ON cr.creator_id = c.creator_id {where}"
    ), cp)).scalar()

    return {
        "campaigns": [{
            "id": r["campaign_id"], "title": r["title"], "status": r["status"],
            "creator_id": r["creator_id"], "creator_name": r["creator_name"] or "Unknown",
            "goal": (r["funding_goal_cents"] or 0) / 100,
            "raised": (r["amount_raised_cents"] or 0) / 100,
            "backers": r["backers"] or 0,
            "category": r["category"], "location": r["location"],
            "time_created": str(r["time_created"]) if r["time_created"] else None,
            "report_count": r["report_count"] or 0,
        } for r in rows],
        "total": total, "limit": limit, "offset": offset,
    }


@router.post("/campaigns/{campaign_id}/delete")
async def delete_campaign(campaign_id: int, admin_id: int,
                          reason: str = "Policy violation",
                          db: AsyncSession = Depends(get_db)):
    await _verify_admin(admin_id, db)
    await db.execute(text("""
        INSERT INTO deleted_campaigns (campaign_id, creator_id, title, status, description, category, location,
            funding_goal_cents, amount_raised_cents, backers, url, time_created, deleted_by, deletion_reason)
        SELECT campaign_id, creator_id, title, status, description, category, location,
            funding_goal_cents, amount_raised_cents, backers, url, time_created, :a, :r
        FROM campaigns WHERE campaign_id = :cid
    """), {"cid": campaign_id, "a": str(admin_id), "r": reason})
    await db.execute(text("DELETE FROM campaigns WHERE campaign_id = :cid"), {"cid": campaign_id})
    try:
        await db.execute(text("DELETE FROM reports WHERE CAST(campaign_id AS TEXT) = :c"),
                         {"c": str(campaign_id)})
    except Exception:
        pass
    await _log(db, admin_id, "delete_campaign", "campaign", str(campaign_id), reason)
    await db.commit()
    return {"status": "deleted", "campaign_id": campaign_id}


@router.post("/campaigns/{campaign_id}/suspend")
async def suspend_campaign(campaign_id: int, admin_id: int,
                           db: AsyncSession = Depends(get_db)):
    await _verify_admin(admin_id, db)
    await db.execute(text("UPDATE campaigns SET status = 'suspended' WHERE campaign_id = :c"),
                     {"c": campaign_id})
    await _log(db, admin_id, "suspend_campaign", "campaign", str(campaign_id))
    await db.commit()
    return {"status": "suspended", "campaign_id": campaign_id}


@router.post("/campaigns/{campaign_id}/reinstate")
async def reinstate_campaign(campaign_id: int, admin_id: int,
                             db: AsyncSession = Depends(get_db)):
    await _verify_admin(admin_id, db)
    await db.execute(text("UPDATE campaigns SET status = 'active' WHERE campaign_id = :c"),
                     {"c": campaign_id})
    await _log(db, admin_id, "reinstate_campaign", "campaign", str(campaign_id))
    await db.commit()
    return {"status": "reinstated", "campaign_id": campaign_id}


# ─── Users ─────────────────────────────────────────────────────────────────

@router.get("/users")
async def list_users(admin_id: int, search: str = "", filter_type: str = "all",
                     limit: int = 50, offset: int = 0,
                     db: AsyncSession = Depends(get_db)):
    await _verify_admin(admin_id, db)
    clauses, params = [], {"limit": limit, "offset": offset}
    if search:
        clauses.append("(LOWER(COALESCE(cr.name,'')) LIKE LOWER(:s) OR LOWER(COALESCE(cr.email,'')) LIKE LOWER(:s))")
        params["s"] = f"%{search}%"
    if filter_type == "blocked":
        clauses.append("b.block_id IS NOT NULL")
    elif filter_type == "active":
        clauses.append("b.block_id IS NULL")
    where = "WHERE " + " AND ".join(clauses) if clauses else ""

    r = await db.execute(text(f"""
        SELECT cr.creator_id, cr.name, cr.email, cr.time_creation AS time_created,
               b.block_id, b.reason AS block_reason, b.blocked_at,
               (SELECT COUNT(*) FROM campaigns WHERE creator_id = cr.creator_id) AS campaign_count,
               (SELECT COALESCE(SUM(amount_raised_cents),0) FROM campaigns WHERE creator_id = cr.creator_id) AS raised_cents
        FROM creators cr
        LEFT JOIN blocked_users b ON b.creator_id = cr.creator_id
        {where}
        ORDER BY cr.time_creation DESC NULLS LAST
        LIMIT :limit OFFSET :offset
    """), params)
    rows = r.mappings().all()

    cp = {k: v for k, v in params.items() if k not in ("limit", "offset")}
    total = (await db.execute(text(
        f"SELECT COUNT(*) FROM creators cr LEFT JOIN blocked_users b ON b.creator_id = cr.creator_id {where}"
    ), cp)).scalar()

    return {
        "users": [{
            "creator_id": r["creator_id"],
            "name": r["name"] or "Unknown",
            "email": r["email"],
            "time_created": str(r["time_created"]) if r["time_created"] else None,
            "is_blocked": r["block_id"] is not None,
            "block_reason": r["block_reason"],
            "blocked_at": str(r["blocked_at"]) if r["blocked_at"] else None,
            "campaign_count": r["campaign_count"] or 0,
            "total_raised": (r["raised_cents"] or 0) / 100,
        } for r in rows],
        "total": total,
    }


@router.post("/users/{creator_id}/block")
async def block_user(creator_id: str, admin_id: int,
                     reason: str = "Policy violation",
                     db: AsyncSession = Depends(get_db)):
    await _verify_admin(admin_id, db)
    existing = await db.execute(
        text("SELECT block_id FROM blocked_users WHERE creator_id = :u"), {"u": creator_id}
    )
    if existing.first():
        raise HTTPException(status_code=409, detail="User already blocked")
    await db.execute(
        text("INSERT INTO blocked_users (creator_id, reason, blocked_by) VALUES (:u, :r, :a)"),
        {"u": creator_id, "r": reason, "a": admin_id},
    )
    await _log(db, admin_id, "block_user", "user", creator_id, reason)
    await db.commit()
    return {"status": "blocked", "creator_id": creator_id}


@router.post("/users/{creator_id}/unblock")
async def unblock_user(creator_id: str, admin_id: int,
                       db: AsyncSession = Depends(get_db)):
    await _verify_admin(admin_id, db)
    await db.execute(
        text("DELETE FROM blocked_users WHERE creator_id = :u"), {"u": creator_id}
    )
    await _log(db, admin_id, "unblock_user", "user", creator_id)
    await db.commit()
    return {"status": "unblocked", "creator_id": creator_id}


# ─── Reports (moderation queue) ───────────────────────────────────────────

@router.get("/reports")
async def list_reports(admin_id: int, db: AsyncSession = Depends(get_db)):
    await _verify_admin(admin_id, db)

    rc = await db.execute(text("""
        SELECT c.campaign_id, c.title, c.status, c.creator_id, cr.name AS creator_name,
               c.funding_goal_cents, c.amount_raised_cents, c.backers,
               COUNT(r.report_id) AS report_count,
               STRING_AGG(DISTINCT COALESCE(r.reason,''), ', ') AS reasons
        FROM campaigns c
        JOIN reports r ON CAST(r.campaign_id AS TEXT) = CAST(c.campaign_id AS TEXT)
        LEFT JOIN creators cr ON cr.creator_id = c.creator_id
        GROUP BY c.campaign_id, c.title, c.status, c.creator_id, cr.name,
                 c.funding_goal_cents, c.amount_raised_cents, c.backers
        ORDER BY report_count DESC
    """))
    cc = await db.execute(text("""
        SELECT co.comment_id, co.comment_text, co.creator_id, cr.name AS commenter_name,
               co.campaign_id, ca.title AS campaign_title, co.is_hidden, co.time_created
        FROM comments co
        LEFT JOIN creators cr ON cr.creator_id = co.creator_id
        LEFT JOIN campaigns ca ON ca.campaign_id = co.campaign_id
        WHERE co.is_hidden = false
        ORDER BY co.time_created DESC
        LIMIT 50
    """))

    return {
        "campaigns": [{
            "id": r["campaign_id"], "title": r["title"], "status": r["status"],
            "creator_id": r["creator_id"], "creator_name": r["creator_name"] or "Unknown",
            "raised": (r["amount_raised_cents"] or 0) / 100,
            "goal":   (r["funding_goal_cents"] or 0) / 100,
            "backers": r["backers"] or 0,
            "report_count": r["report_count"],
            "reasons": r["reasons"],
        } for r in rc.mappings()],
        "comments": [{
            "id": r["comment_id"], "text": r["comment_text"],
            "creator_id": r["creator_id"], "commenter_name": r["commenter_name"] or "Unknown",
            "campaign_id": r["campaign_id"],
            "campaign_title": r["campaign_title"] or f"Campaign #{r['campaign_id']}",
            "time_created": str(r["time_created"]) if r["time_created"] else None,
        } for r in cc.mappings()],
    }


@router.post("/comments/{comment_id}/delete")
async def delete_comment(comment_id: int, admin_id: int,
                         db: AsyncSession = Depends(get_db)):
    await _verify_admin(admin_id, db)
    await db.execute(text("DELETE FROM comments WHERE comment_id = :c"), {"c": comment_id})
    await _log(db, admin_id, "delete_comment", "comment", str(comment_id))
    await db.commit()
    return {"status": "deleted", "comment_id": comment_id}


# ─── Transactions ─────────────────────────────────────────────────────────

@router.get("/transactions")
async def list_transactions(admin_id: int, status: str = "all", search: str = "",
                            limit: int = 50, offset: int = 0,
                            db: AsyncSession = Depends(get_db)):
    await _verify_admin(admin_id, db)
    clauses, params = [], {"limit": limit, "offset": offset}
    if status != "all":
        clauses.append("d.status = :st"); params["st"] = status
    if search:
        clauses.append("(LOWER(COALESCE(c.title,'')) LIKE LOWER(:s) OR LOWER(COALESCE(d.donor_email,'')) LIKE LOWER(:s))")
        params["s"] = f"%{search}%"
    where = "WHERE " + " AND ".join(clauses) if clauses else ""

    r = await db.execute(text(f"""
        SELECT d.donation_id, d.amount, d.status, d.time_created, d.donor_email,
               d.is_anonymous, d.stripe_payment_intent_id, d.platform_fee,
               c.title AS campaign_title, c.campaign_id
        FROM donations d
        LEFT JOIN campaigns c ON c.campaign_id = d.campaign_id
        {where}
        ORDER BY d.time_created DESC
        LIMIT :limit OFFSET :offset
    """), params)
    rows = r.mappings().all()

    cp = {k: v for k, v in params.items() if k not in ("limit", "offset")}
    total = (await db.execute(text(
        f"SELECT COUNT(*) FROM donations d LEFT JOIN campaigns c ON c.campaign_id = d.campaign_id {where}"
    ), cp)).scalar()

    summary = await db.execute(text("""
        SELECT COALESCE(SUM(amount),0) AS total,
               COALESCE(SUM(platform_fee),0) AS fees,
               COUNT(*) AS count
        FROM donations WHERE status='succeeded'
    """))
    sm = summary.mappings().first()

    return {
        "transactions": [{
            "id": r["donation_id"], "amount": float(r["amount"]),
            "status": r["status"], "time": str(r["time_created"]),
            "donor": "Anonymous" if r["is_anonymous"] else (r["donor_email"] or "Unknown"),
            "campaign_title": r["campaign_title"] or "Unknown",
            "campaign_id": r["campaign_id"],
            "stripe_id": r["stripe_payment_intent_id"],
            "platform_fee": float(r["platform_fee"] or 0),
        } for r in rows],
        "total": total,
        "summary": {
            "total_volume": float(sm["total"]),
            "total_fees":   float(sm["fees"]),
            "total_count":  sm["count"],
        },
    }


# ─── Activity log ─────────────────────────────────────────────────────────

@router.get("/activity")
async def list_activity(admin_id: int, limit: int = 100,
                        db: AsyncSession = Depends(get_db)):
    await _verify_admin(admin_id, db)
    # Resilient: if admin_activity_log table doesn't exist yet, return empty.
    try:
        exists = await db.execute(text("SELECT to_regclass('public.admin_activity_log')"))
        if exists.scalar() is None:
            return {"activity": [], "notice": "Activity log table not yet provisioned."}
        r = await db.execute(text("""
            SELECT l.log_id, l.action, l.target_type, l.target_id, l.details, l.created_at,
                   sa.first_name, sa.last_name
            FROM admin_activity_log l
            LEFT JOIN site_admins sa ON sa.admin_id = l.admin_id
            ORDER BY l.created_at DESC
            LIMIT :limit
        """), {"limit": limit})
        return {
            "activity": [{
                "id": row["log_id"],
                "action": row["action"],
                "target_type": row["target_type"],
                "target_id": row["target_id"],
                "details": row["details"],
                "time": str(row["created_at"]),
                "admin_name": f"{row['first_name'] or ''} {row['last_name'] or ''}".strip() or "Unknown",
            } for row in r.mappings()]
        }
    except Exception:
        await db.rollback()
        return {"activity": [], "notice": "Activity log temporarily unavailable."}


# ============================================================================
# v8 ADDITIONS — Approval workflow + tiered ban system
# ============================================================================

class BanAction(BaseModel):
    ban_type: str  # 'warning' | 'soft_ban' | 'full_ban'
    reason: str | None = None


class RejectCampaign(BaseModel):
    reason: str


# ─── Campaign approval workflow ──────────────────────────────────────────────

@router.get("/pending-campaigns")
async def list_pending_campaigns(admin_id: int, db: AsyncSession = Depends(get_db)):
    """Campaigns awaiting admin approval."""
    await _verify_admin(admin_id, db)
    r = await db.execute(text("""
        SELECT c.campaign_id, c.title, c.description, c.category, c.location,
               c.funding_goal_cents, c.status, c.creator_id, c.time_created,
               cr.name AS creator_name, cr.email AS creator_email
        FROM campaigns c
        LEFT JOIN creators cr ON cr.creator_id = c.creator_id
        WHERE c.status = 'pending_review'
        ORDER BY c.time_created DESC
    """))
    rows = r.mappings().all()
    return {
        "count": len(rows),
        "pending": [{
            "campaign_id": row["campaign_id"],
            "title": row["title"],
            "description": row["description"],
            "category": row["category"],
            "location": row["location"],
            "funding_goal": (row["funding_goal_cents"] or 0) / 100,
            "creator_id": row["creator_id"],
            "creator_name": row["creator_name"] or "Unknown",
            "creator_email": row["creator_email"],
            "submitted_at": str(row["time_created"]) if row["time_created"] else None,
        } for row in rows]
    }


@router.post("/campaigns/{campaign_id}/approve")
async def approve_campaign(campaign_id: int, admin_id: int, db: AsyncSession = Depends(get_db)):
    """Approve a pending campaign so it becomes visible to the public."""
    await _verify_admin(admin_id, db)
    r = await db.execute(text("""
        UPDATE campaigns
        SET status = 'active', reviewed_by = :aid, reviewed_at = NOW(), rejected_reason = NULL
        WHERE campaign_id = :cid AND status = 'pending_review'
        RETURNING campaign_id, title
    """), {"cid": campaign_id, "aid": admin_id})
    row = r.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Campaign not found or already reviewed")
    await _log(db, admin_id, "approve_campaign", "campaign", str(campaign_id), f"Approved: {row['title']}")
    await db.commit()
    return {"status": "approved", "campaign_id": campaign_id, "title": row["title"]}


@router.post("/campaigns/{campaign_id}/reject")
async def reject_campaign(campaign_id: int, admin_id: int, data: RejectCampaign,
                           db: AsyncSession = Depends(get_db)):
    """Reject a pending campaign with a reason — campaign stays hidden from public."""
    await _verify_admin(admin_id, db)
    r = await db.execute(text("""
        UPDATE campaigns
        SET status = 'rejected', reviewed_by = :aid, reviewed_at = NOW(), rejected_reason = :reason
        WHERE campaign_id = :cid AND status = 'pending_review'
        RETURNING campaign_id, title
    """), {"cid": campaign_id, "aid": admin_id, "reason": data.reason})
    row = r.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Campaign not found or already reviewed")
    await _log(db, admin_id, "reject_campaign", "campaign", str(campaign_id),
               f"Rejected: {row['title']} — {data.reason}")
    await db.commit()
    return {"status": "rejected", "campaign_id": campaign_id, "reason": data.reason}


# ─── Tiered ban system (warning → soft_ban → full_ban) ──────────────────────

@router.post("/users/{creator_id}/moderate")
async def moderate_user(creator_id: str, admin_id: int, data: BanAction,
                         db: AsyncSession = Depends(get_db)):
    """
    Apply a moderation action to a user.

    ban_type options:
      • 'warning'   — issue a warning. If user reaches 3 warnings, auto-promoted to full_ban.
      • 'soft_ban'  — user can never comment anywhere again (account stays open).
      • 'full_ban'  — user's account deactivated, locked out entirely.
    """
    await _verify_admin(admin_id, db)

    if data.ban_type not in ("warning", "soft_ban", "full_ban"):
        raise HTTPException(status_code=400, detail="ban_type must be warning, soft_ban, or full_ban")

    # Verify creator exists
    r = await db.execute(text("SELECT creator_id, name FROM creators WHERE creator_id = :id"), {"id": creator_id})
    creator = r.mappings().first()
    if not creator:
        raise HTTPException(status_code=404, detail="User not found")

    reason = data.reason or "Moderation action by site admin"

    if data.ban_type == "warning":
        # Increment warning counter
        r = await db.execute(text("""
            UPDATE creators SET warning_count = COALESCE(warning_count, 0) + 1
            WHERE creator_id = :id
            RETURNING warning_count
        """), {"id": creator_id})
        new_count = r.scalar()

        # Record the warning in blocked_users (non-unique — accumulates)
        await db.execute(text("""
            INSERT INTO blocked_users (creator_id, reason, blocked_by, ban_type)
            VALUES (:cid, :r, :aid, 'warning')
        """), {"cid": creator_id, "r": reason, "aid": admin_id})

        # Auto-promote at 3rd warning
        if new_count >= 3:
            # Ensure no existing active ban first
            await db.execute(text("""
                DELETE FROM blocked_users
                WHERE creator_id = :cid AND ban_type IN ('soft_ban', 'full_ban')
            """), {"cid": creator_id})
            await db.execute(text("""
                INSERT INTO blocked_users (creator_id, reason, blocked_by, ban_type)
                VALUES (:cid, :r, :aid, 'full_ban')
            """), {"cid": creator_id, "r": f"Auto-escalated after {new_count} warnings", "aid": admin_id})
            await _log(db, admin_id, "auto_full_ban", "user", creator_id,
                       f"Auto-full-ban after {new_count} warnings for {creator['name']}")
            await db.commit()
            return {"status": "full_ban", "auto_escalated": True, "warning_count": new_count,
                    "message": f"{creator['name']} auto-banned after {new_count} warnings"}

        await _log(db, admin_id, "warning", "user", creator_id,
                   f"Warning #{new_count} issued to {creator['name']}: {reason}")
        await db.commit()
        return {"status": "warning", "warning_count": new_count,
                "message": f"Warning #{new_count} of 3 issued"}

    # soft_ban or full_ban — upsert the active ban
    await db.execute(text("""
        DELETE FROM blocked_users
        WHERE creator_id = :cid AND ban_type IN ('soft_ban', 'full_ban')
    """), {"cid": creator_id})
    await db.execute(text("""
        INSERT INTO blocked_users (creator_id, reason, blocked_by, ban_type)
        VALUES (:cid, :r, :aid, :bt)
    """), {"cid": creator_id, "r": reason, "aid": admin_id, "bt": data.ban_type})

    action = "soft_ban" if data.ban_type == "soft_ban" else "full_ban"
    await _log(db, admin_id, action, "user", creator_id,
               f"{action} applied to {creator['name']}: {reason}")
    await db.commit()
    return {"status": data.ban_type, "creator_id": creator_id}


@router.post("/users/{creator_id}/lift-ban")
async def lift_ban(creator_id: str, admin_id: int, db: AsyncSession = Depends(get_db)):
    """Remove all active bans (soft and full) for a user. Warning history preserved."""
    await _verify_admin(admin_id, db)
    r = await db.execute(text("""
        DELETE FROM blocked_users
        WHERE creator_id = :cid AND ban_type IN ('soft_ban', 'full_ban')
        RETURNING ban_id
    """), {"cid": creator_id})
    removed = len(r.all())
    await _log(db, admin_id, "lift_ban", "user", creator_id, f"Lifted bans ({removed} rows)")
    await db.commit()
    return {"status": "lifted", "removed": removed}


@router.post("/users/{creator_id}/reset-warnings")
async def reset_warnings(creator_id: str, admin_id: int, db: AsyncSession = Depends(get_db)):
    """Clear a user's warning history."""
    await _verify_admin(admin_id, db)
    await db.execute(text("UPDATE creators SET warning_count = 0 WHERE creator_id = :cid"),
                     {"cid": creator_id})
    await db.execute(text("DELETE FROM blocked_users WHERE creator_id = :cid AND ban_type = 'warning'"),
                     {"cid": creator_id})
    await _log(db, admin_id, "reset_warnings", "user", creator_id, "Warning count reset to 0")
    await db.commit()
    return {"status": "reset"}


# ─── Public-site enforcement helper ─────────────────────────────────────────

@router.get("/check-comment-permission")
async def check_comment_permission(creator_id: str, db: AsyncSession = Depends(get_db)):
    """
    Public endpoint called by the comment-submission flow.
    Returns {can_comment: bool, reason?: str} — frontend uses this to block
    submission UI for soft-banned users.
    """
    r = await db.execute(text("""
        SELECT ban_type, reason FROM blocked_users
        WHERE creator_id = :cid AND ban_type IN ('soft_ban', 'full_ban')
        ORDER BY blocked_at DESC LIMIT 1
    """), {"cid": creator_id})
    row = r.mappings().first()
    if row:
        return {"can_comment": False, "ban_type": row["ban_type"], "reason": row["reason"]}
    return {"can_comment": True}
