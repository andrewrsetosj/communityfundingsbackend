"""
Campaign routes — full lifecycle management
Create, edit, publish, cancel, search, filter, featured
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func as sqlfunc, or_, text
from sqlalchemy.orm import selectinload
from typing import Optional, List
from decimal import Decimal
from datetime import datetime, timezone
import re
from pydantic import BaseModel

from app.database import get_db
from app.auth import get_current_user, get_optional_user
from app.models.models import Campaign, CampaignStatus, User, Donation, DonationStatus
from app.models.schemas import (
    CampaignCreate, CampaignUpdate, CampaignResponse, CampaignListResponse,
)
import db as db_mod

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


# ── Helpers ────────────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:200]

def build_campaign_response(
    c: Campaign,
    creator_name: Optional[str] = None,
    image_url: Optional[str] = None,
    content_type: Optional[str] = None,
) -> CampaignResponse:
    goal = float(c.goal_amount) if c.goal_amount else 0
    raised = float(c.raised_amount) if c.raised_amount else 0
    pct = round(raised / goal * 100, 1) if goal > 0 else 0.0

    days_left = None
    if c.end_date:
        delta = c.end_date.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc) \
            if c.end_date.tzinfo is None else c.end_date - datetime.now(timezone.utc)
        days_left = max(0, delta.days)

    return CampaignResponse(
        id=c.id,
        title=c.title,
        slug=c.slug,
        description=c.description,
        goal_amount=goal,
        raised_amount=raised,
        creator_id=c.creator_id,
        creator_name=creator_name or (c.creator.name if c.creator else None),
        status=c.status,
        donors_count=c.donors_count or 0,
        category=c.category,
        location=c.location,
        end_date=c.end_date,
        bio=c.bio,
        duration_days=c.duration_days,
        funding_percentage=pct,
        days_left=days_left,
        created_at=c.created_at,
        image_url=image_url,
        content_type=content_type,
    )


# ── List / Search ──────────────────────────────────────────────────────────

@router.get("", response_model=CampaignListResponse)
async def list_campaigns(
    status: Optional[str] = "active",
    category: Optional[str] = None,
    location: Optional[str] = None,
    q: Optional[str] = None,
    sort: Optional[str] = "recent",
    featured: Optional[bool] = None,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=12, le=50),
):
    pool = await db_mod.get_pool()

    where_clauses = []
    params = []
    param_index = 1

    if status:
        where_clauses.append(f"c.status = ${param_index}")
        params.append(status)
        param_index += 1

    if category:
        where_clauses.append(f"c.category = ${param_index}")
        params.append(category)
        param_index += 1

    if location:
        where_clauses.append(f"c.location ILIKE ${param_index}")
        params.append(f"%{location}%")
        param_index += 1

    if q:
        where_clauses.append(f"(c.title ILIKE ${param_index} OR c.description_html ILIKE ${param_index})")
        params.append(f"%{q}%")
        param_index += 1

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    if sort == "popular":
        order_sql = "ORDER BY c.backers DESC, c.time_created DESC"
    elif sort == "ending_soon":
        order_sql = "ORDER BY c.end_date ASC NULLS LAST, c.time_created DESC"
    elif sort == "most_funded":
        order_sql = "ORDER BY c.amount_raised_cents DESC, c.time_created DESC"
    else:
        order_sql = "ORDER BY c.time_created DESC, c.campaign_id DESC"

    offset = (page - 1) * per_page

    async with pool.acquire() as conn:
        count_row = await conn.fetchrow(
            f"""
            SELECT COUNT(*) AS total
            FROM campaigns c
            {where_sql}
            """,
            *params,
        )
        total = count_row["total"] if count_row else 0

        rows = await conn.fetch(
            f"""
            SELECT
                c.campaign_id AS id,
                c.title,
                c.url AS slug,
                c.description_html AS description,
                COALESCE(c.funding_goal_cents, 0) / 100.0 AS goal_amount,
                COALESCE(c.amount_raised_cents, 0) / 100.0 AS raised_amount,
                c.creator_id,
                creator.name AS creator_name,
                c.status,
                COALESCE(c.backers, 0) AS donors_count,
                c.category,
                c.location,
                c.end_date,
                c.bio,
                c.duration_days,
                CASE
                    WHEN COALESCE(c.funding_goal_cents, 0) > 0
                    THEN ROUND((COALESCE(c.amount_raised_cents, 0)::numeric / c.funding_goal_cents::numeric) * 100, 1)
                    ELSE 0
                END AS funding_percentage,
                CASE
                    WHEN c.duration_days IS NULL OR c.time_created IS NULL THEN NULL
                    ELSE GREATEST(0, c.duration_days - FLOOR(EXTRACT(EPOCH FROM (NOW() - c.time_created)) / 86400.0)::int)
                END AS days_left,
                c.time_created AS created_at,
                cp.content_type,
                CASE
                    WHEN cp.s3_bucket IS NOT NULL AND cp.s3_key IS NOT NULL
                    THEN 'https://' || cp.s3_bucket || '.s3.us-east-2.amazonaws.com/' || cp.s3_key
                    ELSE NULL
                END AS image_url
            FROM campaigns c
            LEFT JOIN creators creator
              ON creator.creator_id = c.creator_id
            LEFT JOIN LATERAL (
                SELECT
                    photo_id,
                    s3_bucket,
                    s3_key,
                    content_type,
                    is_primary
                FROM campaign_photos
                WHERE campaign_id = c.campaign_id
                ORDER BY is_primary DESC, photo_id ASC
                LIMIT 1
            ) cp ON true
            {where_sql}
            {order_sql}
            LIMIT ${param_index} OFFSET ${param_index + 1}
            """,
            *params,
            per_page,
            offset,
        )

    return CampaignListResponse(
        campaigns=[dict(row) for row in rows],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/categories")
async def get_categories(db: AsyncSession = Depends(get_db)):
    """Get all categories with campaign counts."""
    result = await db.execute(
        select(Campaign.category, sqlfunc.count(Campaign.id))
        .where(Campaign.status == "active")
        .where(Campaign.category.isnot(None))
        .group_by(Campaign.category)
        .order_by(sqlfunc.count(Campaign.id).desc())
    )
    return [{"name": row[0], "count": row[1]} for row in result.all()]


@router.get("/stats")
async def platform_stats():
    """Get real-time platform statistics."""
    try:
        return await db_mod.get_platform_stats()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/featured", response_model=List[CampaignResponse])
async def get_featured(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Campaign)
        .where(Campaign.status == "active")
        .order_by(Campaign.raised_amount.desc())
        .limit(6)
    )
    return [build_campaign_response(c) for c in result.scalars().all()]


@router.get("/check-slug")
async def check_slug(slug: str, db: AsyncSession = Depends(get_db)):
    """
    Check if a vanity slug is available.
    Returns {"available": bool}.
    """
    candidate = (slug or "").strip()
    if not candidate:
        raise HTTPException(status_code=400, detail="Slug is required")

    # DB schema uses campaigns.campaign_id + campaigns.url, not ORM's id/slug.
    result = await db.execute(
        text("SELECT campaign_id FROM public.campaigns WHERE url = :slug LIMIT 1"),
        {"slug": candidate},
    )
    return {"available": result.first() is None}


@router.get("/my-campaigns")
async def my_campaigns(user: User = Depends(get_current_user)):
    """Get all campaigns created by the authenticated user."""
    pool = await db_mod.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                campaign_id AS id,
                title,
                url AS slug,
                status,
                category,
                COALESCE(funding_goal_cents, 0) / 100.0 AS goal_amount,
                COALESCE(amount_raised_cents, 0) / 100.0 AS raised_amount,
                CASE
                    WHEN COALESCE(funding_goal_cents, 0) > 0
                    THEN ROUND((COALESCE(amount_raised_cents, 0)::numeric / funding_goal_cents::numeric) * 100, 1)
                    ELSE 0
                END AS funding_percentage,
                COALESCE(backers, 0) AS donors_count,
                CASE
                    WHEN duration_days IS NULL OR time_created IS NULL THEN NULL
                    ELSE GREATEST(0, duration_days - FLOOR(EXTRACT(EPOCH FROM (NOW() - time_created)) / 86400.0)::int)
                END AS days_left,
                time_created AS created_at
            FROM public.campaigns
            WHERE creator_id = $1
            ORDER BY time_created DESC, campaign_id DESC
            """,
            user.id,
        )
        return [dict(row) for row in rows]


@router.get("/my-collaborations")
async def my_collaborations(user: User = Depends(get_current_user)):
    """Get campaigns where the authenticated user is an accepted collaborator."""
    pool = await db_mod.get_pool()
    async with pool.acquire() as conn:
        viewer = await conn.fetchrow(
            """
            SELECT email
            FROM creators
            WHERE creator_id = $1
            LIMIT 1
            """,
            user.id,
        )
        if not viewer:
            raise HTTPException(status_code=404, detail="Creator profile not found")

        email = (viewer["email"] or "").strip().lower()
        if not email:
            return []

        rows = await conn.fetch(
            """
            SELECT
                camp.campaign_id,
                camp.title,
                camp.url,
                camp.status,
                camp.category,
                COALESCE(camp.funding_goal_cents, 0) AS funding_goal_cents,
                COALESCE(camp.amount_raised_cents, 0) AS amount_raised_cents,
                COALESCE(camp.backers, 0) AS backers,
                camp.time_created,
                camp.creator_id,
                owner.name AS owner_name,
                owner.last_name AS owner_last_name,
                COALESCE(NULLIF(owner.username, ''), owner.creator_id) AS owner_username
            FROM collaborators coll
            JOIN campaigns camp
              ON camp.campaign_id = coll.campaign_id
            JOIN creators owner
              ON owner.creator_id = camp.creator_id
            WHERE LOWER(coll.email) = $1
              AND LOWER(COALESCE(coll.status, '')) = 'accepted'
            ORDER BY coll.time_created DESC, coll.collaborator_id DESC
            """,
            email,
        )
        return [dict(row) for row in rows]


@router.get("/my-organizations")
async def my_organizations(user: User = Depends(get_current_user)):
    """Get organizations the user belongs to, with their campaigns."""
    pool = await db_mod.get_pool()
    async with pool.acquire() as conn:
        orgs = await conn.fetch(
            """
            SELECT c.creator_id, c.name, c.last_name, c.bio
            FROM organization_members om
            JOIN creators c ON c.creator_id = om.organization_id
            WHERE om.member_id = $1
            """,
            user.id,
        )
        result = []
        for org in orgs:
            campaigns = await conn.fetch(
                """
                SELECT campaign_id, title, url, status, category,
                       funding_goal_cents, amount_raised_cents, backers, time_created
                FROM campaigns
                WHERE creator_id = $1
                ORDER BY time_created DESC
                """,
                org["creator_id"],
            )
            result.append({
                "organization_id": org["creator_id"],
                "name": org["name"] or "",
                "bio": org["bio"] or "",
                "campaigns": [dict(c) for c in campaigns],
            })
        return result


# ── Drafts ─────────────────────────────────────────────────────────────────

@router.get("/my-drafts")
async def my_drafts(user: User = Depends(get_current_user)):
    """List all draft campaigns for the authenticated user."""
    try:
        return await db_mod.list_draft_campaigns(user.id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class CollaboratorInviteActionRequest(BaseModel):
    action: str | None = None


@router.get("/invites/received")
async def list_received_collaborator_invites(user: User = Depends(get_current_user)):
    """List pending collaborator invites for the authenticated creator."""
    pool = await db_mod.get_pool()
    async with pool.acquire() as conn:
        viewer = await conn.fetchrow(
            """
            SELECT creator_id, email
            FROM creators
            WHERE creator_id = $1
            LIMIT 1
            """,
            user.id,
        )
        if not viewer:
            raise HTTPException(status_code=404, detail="Creator profile not found")

        email = (viewer["email"] or "").strip().lower()
        if not email:
            return {"invites": []}

        rows = await conn.fetch(
            """
            SELECT
                coll.collaborator_id,
                coll.campaign_id,
                coll.email,
                coll.status,
                coll.time_created,
                camp.title AS campaign_title,
                camp.url AS campaign_url,
                camp.status AS campaign_status,
                camp.category AS campaign_category,
                camp.funding_goal_cents,
                camp.amount_raised_cents,
                camp.backers,
                camp.time_created AS campaign_time_created,
                camp.creator_id,
                COALESCE(NULLIF(owner.username, ''), owner.creator_id) AS creator_username,
                owner.name AS creator_name,
                owner.last_name AS creator_last_name,
                owner.avatar_url AS creator_avatar_url
            FROM collaborators coll
            JOIN campaigns camp
              ON camp.campaign_id = coll.campaign_id
            JOIN creators owner
              ON owner.creator_id = camp.creator_id
            WHERE LOWER(coll.email) = $1
              AND LOWER(COALESCE(coll.status, 'pending')) = 'pending'
            ORDER BY coll.time_created DESC, coll.collaborator_id DESC
            """,
            email,
        )

        invites = []
        for row in rows:
            invites.append({
                "collaborator_id": row["collaborator_id"],
                "email": row["email"],
                "status": row["status"],
                "time_created": row["time_created"],
                "campaign": {
                    "campaign_id": row["campaign_id"],
                    "title": row["campaign_title"],
                    "url": row["campaign_url"],
                    "status": row["campaign_status"],
                    "category": row["campaign_category"],
                    "funding_goal_cents": row["funding_goal_cents"],
                    "amount_raised_cents": row["amount_raised_cents"],
                    "backers": row["backers"],
                    "time_created": row["campaign_time_created"],
                    "creator_id": row["creator_id"],
                },
                "inviter": {
                    "creator_id": row["creator_id"],
                    "username": row["creator_username"],
                    "name": row["creator_name"],
                    "last_name": row["creator_last_name"],
                    "avatar_url": row["creator_avatar_url"],
                },
            })
        return {"invites": invites}


@router.post("/invites/{collaborator_id}/accept")
async def accept_collaborator_invite(
    collaborator_id: int,
    user: User = Depends(get_current_user),
):
    pool = await db_mod.get_pool()
    async with pool.acquire() as conn:
        viewer = await conn.fetchrow(
            """
            SELECT creator_id, email
            FROM creators
            WHERE creator_id = $1
            LIMIT 1
            """,
            user.id,
        )
        if not viewer:
            raise HTTPException(status_code=404, detail="Creator profile not found")

        email = (viewer["email"] or "").strip().lower()
        invite = await conn.fetchrow(
            """
            SELECT collaborator_id, campaign_id, email, status
            FROM collaborators
            WHERE collaborator_id = $1
            LIMIT 1
            """,
            collaborator_id,
        )
        if not invite:
            raise HTTPException(status_code=404, detail="Invite not found")
        if (invite["email"] or "").strip().lower() != email:
            raise HTTPException(status_code=403, detail="This invite is not for your account")

        status = (invite["status"] or "pending").strip().lower()
        if status == 'accepted':
            return {"ok": True, "status": "accepted", "collaborator_id": collaborator_id}
        if status == 'declined':
            await conn.execute("DELETE FROM collaborators WHERE collaborator_id = $1", collaborator_id)
            raise HTTPException(status_code=404, detail="Invite is no longer available")

        await conn.execute(
            """
            UPDATE collaborators
            SET status = 'accepted'
            WHERE collaborator_id = $1
            """,
            collaborator_id,
        )

    return {"ok": True, "status": "accepted", "collaborator_id": collaborator_id}


@router.post("/invites/{collaborator_id}/decline")
async def decline_collaborator_invite(
    collaborator_id: int,
    user: User = Depends(get_current_user),
):
    pool = await db_mod.get_pool()
    async with pool.acquire() as conn:
        viewer = await conn.fetchrow(
            """
            SELECT creator_id, email
            FROM creators
            WHERE creator_id = $1
            LIMIT 1
            """,
            user.id,
        )
        if not viewer:
            raise HTTPException(status_code=404, detail="Creator profile not found")

        email = (viewer["email"] or "").strip().lower()
        invite = await conn.fetchrow(
            """
            SELECT collaborator_id, email
            FROM collaborators
            WHERE collaborator_id = $1
            LIMIT 1
            """,
            collaborator_id,
        )
        if not invite:
            raise HTTPException(status_code=404, detail="Invite not found")
        if (invite["email"] or "").strip().lower() != email:
            raise HTTPException(status_code=403, detail="This invite is not for your account")

        await conn.execute("DELETE FROM collaborators WHERE collaborator_id = $1", collaborator_id)

    return {"ok": True, "status": "declined", "collaborator_id": collaborator_id}


@router.put("/drafts")
async def save_draft(data: dict, user: User = Depends(get_current_user)):
    """Create or update a draft campaign."""
    data["creator_id"] = user.id
    try:
        return await db_mod.upsert_draft_campaign(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/drafts/{campaign_id}/photos")
async def replace_draft_photos(
    campaign_id: int,
    data: dict,
    user: User = Depends(get_current_user),
):
    """Persist campaign_photos after files are stored in S3."""
    try:
        await db_mod.replace_campaign_photos(
            campaign_id, user.id, data.get("photos") or []
        )
        return {"ok": True, "campaign_id": campaign_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/drafts/{campaign_id}")
async def delete_draft(campaign_id: int, user: User = Depends(get_current_user)):
    """Hard-delete a draft campaign and its related data."""
    pool = await db_mod.get_pool()
    async with pool.acquire() as conn:
        # Verify it's a draft owned by this user
        row = await conn.fetchrow(
            "SELECT status, creator_id FROM campaigns WHERE campaign_id = $1",
            campaign_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Draft not found")
        if row["creator_id"] != user.id:
            raise HTTPException(status_code=403, detail="Not your draft")
        if row["status"] != "draft":
            raise HTTPException(status_code=400, detail="Only drafts can be deleted")

        # Delete related rows first, then the campaign
        await conn.execute("DELETE FROM faqs WHERE campaign_id = $1", campaign_id)
        await conn.execute("DELETE FROM rewards WHERE campaign_id = $1", campaign_id)
        await conn.execute("DELETE FROM collaborators WHERE campaign_id = $1", campaign_id)
        await conn.execute("DELETE FROM campaign_photos WHERE campaign_id = $1", campaign_id)
        await conn.execute("DELETE FROM campaigns WHERE campaign_id = $1", campaign_id)

    return {"status": "deleted", "campaign_id": campaign_id}


@router.get("/drafts/{campaign_id}")
async def get_draft(campaign_id: int, user: User = Depends(get_current_user)):
    """Get a single draft campaign with all related data."""
    result = await db_mod.get_draft_campaign(campaign_id, user.id)
    if not result:
        raise HTTPException(status_code=404, detail="Draft not found")
    return result


# ── Finalize from create-project wizard ─────────────────────────────────────

@router.post("/finalize")
async def finalize_campaign(data: dict):
    """
    Submit create-project draft and write campaigns/faqs/rewards/collaborators.
    """
    try:
        return await db_mod.finalize_campaign(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Get by ID or slug ──────────────────────────────────────────────────────

@router.get("/{campaign_id_or_slug}", response_model=CampaignResponse)
async def get_campaign(campaign_id_or_slug: str, db: AsyncSession = Depends(get_db)):
    """Get campaign by ID or URL slug."""
    result = await db.execute(
        select(Campaign).options(selectinload(Campaign.creator)).where(
            or_(Campaign.id == campaign_id_or_slug, Campaign.slug == campaign_id_or_slug)
        )
    )
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return build_campaign_response(campaign)


# ── Create ─────────────────────────────────────────────────────────────────

@router.post("", response_model=CampaignResponse, status_code=201)
async def create_campaign(
    data: CampaignCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new campaign (starts as draft)."""
    slug = slugify(data.title)

    # Ensure unique slug
    existing = await db.execute(select(Campaign).where(Campaign.slug == slug))
    if existing.scalar_one_or_none():
        slug = f"{slug}-{str(user.id)[:8]}"

    campaign = Campaign(
        title=data.title,
        slug=slug,
        description=data.description,
        goal_amount=int(Decimal(str(data.goal_amount)) * 100) if data.goal_amount else 0,
        creator_id=user.id,
        status="draft",
        category=data.category,
        location=data.location,
        end_date=datetime.fromisoformat(data.end_date.replace("Z", "+00:00")) if data.end_date else None,
    )
    db.add(campaign)
    await db.flush()

    return build_campaign_response(campaign, creator_name=user.name)


# ── Edit ───────────────────────────────────────────────────────────────────

@router.put("/{campaign_id}", response_model=CampaignResponse)
async def update_campaign(
    campaign_id: str,
    data: CampaignUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Edit a campaign. Only the creator can edit. Cannot edit funded/cancelled campaigns."""
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.creator_id != user.id:
        raise HTTPException(status_code=403, detail="Not your campaign")
    if campaign.status in ("funded", "cancelled", "suspended"):
        raise HTTPException(status_code=400, detail=f"Cannot edit a {campaign.status} campaign")

    if data.title is not None:
        campaign.title = data.title
    if data.description is not None:
        campaign.description = data.description
    if data.category is not None:
        campaign.category = data.category
    if data.location is not None:
        campaign.location = data.location
    if data.end_date is not None:
        campaign.end_date = datetime.fromisoformat(data.end_date.replace("Z", "+00:00"))

    await db.flush()
    return build_campaign_response(campaign, creator_name=user.name)


# ── Publish ────────────────────────────────────────────────────────────────

@router.post("/{campaign_id}/publish", response_model=CampaignResponse)
async def publish_campaign(
    campaign_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Publish a draft campaign (makes it active and accepting donations)."""
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.creator_id != user.id:
        raise HTTPException(status_code=403, detail="Not your campaign")
    if campaign.status != "draft":
        raise HTTPException(status_code=400, detail="Only draft campaigns can be published")

    campaign.status = "active"
    await db.flush()
    return build_campaign_response(campaign, creator_name=user.name)


# ── Cancel ─────────────────────────────────────────────────────────────────

@router.post("/{campaign_id}/cancel")
async def cancel_campaign(
    campaign_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a campaign. Only creator. Cannot cancel if already funded."""
    result = await db.execute(
        select(Campaign).where(Campaign.id == campaign_id)
    )
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.creator_id != user.id:
        raise HTTPException(status_code=403, detail="Not your campaign")
    if campaign.status == "funded":
        raise HTTPException(status_code=400, detail="Cannot cancel a funded campaign")

    campaign.status = "cancelled"
    await db.commit()
    return {"status": "cancelled", "campaign_id": campaign_id}