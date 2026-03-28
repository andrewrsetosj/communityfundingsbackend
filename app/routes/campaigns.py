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


def build_campaign_response(c: Campaign, creator_name: Optional[str] = None) -> CampaignResponse:
    goal = float(c.goal_amount) if c.goal_amount else 0
    raised = float(c.raised_amount) if c.raised_amount else 0
    pct = round(raised / goal * 100, 1) if goal > 0 else 0.0

    days_left = None
    if c.end_date:
        delta = c.end_date.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc) \
            if c.end_date.tzinfo is None else c.end_date - datetime.now(timezone.utc)
        days_left = max(0, delta.days)

    return CampaignResponse(
        id=c.id, title=c.title, slug=c.slug,
        description=c.description,
        goal_amount=goal, raised_amount=raised,
        creator_id=c.creator_id,
        creator_name=creator_name or (c.creator.name if c.creator else None),
        status=c.status,
        donors_count=c.donors_count or 0,
        category=c.category, location=c.location,
        end_date=c.end_date,
        bio=c.bio, duration_days=c.duration_days,
        funding_percentage=pct, days_left=days_left,
        created_at=c.created_at,
    )


# ── List / Search ──────────────────────────────────────────────────────────

@router.get("", response_model=CampaignListResponse)
async def list_campaigns(
    status: Optional[str] = "active",
    category: Optional[str] = None,
    location: Optional[str] = None,
    q: Optional[str] = None,
    sort: Optional[str] = "recent",  # recent | popular | ending_soon | most_funded
    featured: Optional[bool] = None,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=12, le=50),
    db: AsyncSession = Depends(get_db),
):
    """List campaigns with filtering, search, and sorting."""
    query = select(Campaign).options(selectinload(Campaign.creator))

    # Filters
    if status:
        query = query.where(Campaign.status == status)
    if category:
        query = query.where(Campaign.category == category)
    if location:
        query = query.where(Campaign.location.ilike(f"%{location}%"))
    if q:
        search = f"%{q}%"
        query = query.where(
            or_(Campaign.title.ilike(search), Campaign.description.ilike(search))
        )

    # Count total
    count_q = select(sqlfunc.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    # Sort
    if sort == "popular":
        query = query.order_by(Campaign.donors_count.desc())
    elif sort == "ending_soon":
        query = query.where(Campaign.end_date.isnot(None)).order_by(Campaign.end_date.asc())
    elif sort == "most_funded":
        query = query.order_by(Campaign.raised_amount.desc())
    else:
        query = query.order_by(Campaign.created_at.desc())

    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    result = await db.execute(query)
    campaigns = result.scalars().all()

    return CampaignListResponse(
        campaigns=[build_campaign_response(c) for c in campaigns],
        total=total, page=page, per_page=per_page,
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


@router.get("/my-campaigns", response_model=List[CampaignResponse])
async def my_campaigns(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all campaigns created by the authenticated user."""
    result = await db.execute(
        select(Campaign).where(Campaign.creator_id == user.id).order_by(Campaign.created_at.desc())
    )
    return [build_campaign_response(c, creator_name=user.name) for c in result.scalars().all()]


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