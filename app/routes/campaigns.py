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
from datetime import datetime, timezone, timedelta
import re

from app.database import get_db
from app.auth import get_current_user, get_optional_user
from app.models.models import Campaign, CampaignStatus, User, Donation, DonationStatus
from app.models.schemas import (
    CampaignCreate, CampaignUpdate, CampaignResponse, CampaignListResponse,
    CampaignFinalize,
)

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
        short_description=c.short_description, description=c.description,
        goal_amount=goal, raised_amount=raised,
        image_url=c.image_url, video_url=c.video_url,
        creator_id=c.creator_id,
        creator_name=creator_name or (c.creator.name if c.creator else None),
        status=c.status.value if isinstance(c.status, CampaignStatus) else c.status,
        donors_count=c.donors_count or 0,
        category=c.category, location=c.location,
        end_date=c.end_date, is_featured=c.is_featured or False,
        funding_percentage=pct, days_left=days_left,
        created_at=c.created_at,
    )


# ── List / Search ──────────────────────────────────────────────────────────

@router.get("", response_model=CampaignListResponse)
async def list_campaigns(
    status: Optional[str] = "active",
    category: Optional[str] = None,
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
    if featured is not None:
        query = query.where(Campaign.is_featured == featured)
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
        .where(Campaign.status == CampaignStatus.ACTIVE)
        .where(Campaign.category.isnot(None))
        .group_by(Campaign.category)
        .order_by(sqlfunc.count(Campaign.id).desc())
    )
    return [{"name": row[0], "count": row[1]} for row in result.all()]


@router.get("/featured", response_model=List[CampaignResponse])
async def get_featured(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Campaign)
        .where(Campaign.is_featured == True, Campaign.status == CampaignStatus.ACTIVE)
        .order_by(Campaign.raised_amount.desc())
        .limit(6)
    )
    return [build_campaign_response(c) for c in result.scalars().all()]


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


# ── Finalize (create-project Submit for Review) ─────────────────────────────
# Inserts into public.campaigns using your DDL:
#   campaign_id bigserial, creator_id text, title, status, time_created, url,
#   updated_at, description, category, location, funding_goal_cents, duration_days,
#   amount_raised_cents, backers, end_date.  FK: creator_id → creators(creator_id).

def _sanitize_url_slug(raw: str) -> str:
    """Lowercase, alphanumeric and hyphens only, for campaigns.url (unique)."""
    s = (raw or "").strip().lower()
    s = re.sub(r"[^a-z0-9-]", "", s)
    return s or "campaign"


@router.post("/finalize")
async def finalize_campaign(
    data: CampaignFinalize,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
):
    """
    Submit campaign draft to the database (Submit for Review).
    Inserts into public.campaigns (your DDL: campaign_id bigserial, creator_id text,
    title, status, time_created, url, updated_at, description, category, location,
    funding_goal_cents, duration_days, amount_raised_cents, backers, end_date).
    creator_id must already exist in public.creators(creator_id).
    """
    creator_id = (user.id if user else data.creator_id) or data.creator_id
    if not creator_id:
        raise HTTPException(status_code=400, detail="creator_id required or login required")

    url = _sanitize_url_slug(data.vanity_slug or data.title) or _sanitize_url_slug(data.title)
    # Ensure url is unique: if conflict, append short suffix
    try:
        existing = await db.execute(
            text("SELECT 1 FROM public.campaigns WHERE url = :url LIMIT 1"),
            {"url": url},
        )
        if existing.scalar() is not None:
            url = f"{url}-{creator_id[:8]}"
    except Exception:
        pass  # table might not exist yet or column name differs

    end_date = None
    if data.duration_days and data.duration_days > 0:
        end_date = datetime.now(timezone.utc) + timedelta(days=data.duration_days)

    res = await db.execute(
        text("""
            INSERT INTO public.campaigns (
                creator_id, title, status, time_created, url,
                description, category, "location",
                funding_goal_cents, duration_days, amount_raised_cents, backers, end_date
            )
            VALUES (
                :creator_id, :title, :status, NOW(), :url,
                :description, :category, :location,
                :funding_goal_cents, :duration_days, 0, 0, :end_date
            )
            RETURNING campaign_id, url
        """),
        {
            "creator_id": str(creator_id),
            "title": (data.title or "").strip() or None,
            "status": "pending_review",
            "url": url or None,
            "description": (data.description_html or "").strip() or None,
            "category": (data.category or "").strip() or None,
            "location": (data.location or "").strip() or None,
            "funding_goal_cents": data.funding_goal_cents,
            "duration_days": data.duration_days,
            "end_date": end_date,
        },
    )
    row = res.one()
    campaign_id = row[0]
    final_url = row[1]
    # Transaction is committed when get_db() session context exits

    return {"campaign_id": campaign_id, "slug": final_url}


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
    """
    Create a new campaign.
    Always starts as draft.
    """

    # ─────────────────────────────────────────────
    # Slug handling
    # ─────────────────────────────────────────────
    slug = data.vanity_slug or slugify(data.title)

    existing = await db.execute(
        select(Campaign).where(Campaign.slug == slug)
    )
    if existing.scalar_one_or_none():
        slug = f"{slug}-{str(user.id)[:8]}"

    # ─────────────────────────────────────────────
    # Money conversion (cents → Decimal dollars)
    # ─────────────────────────────────────────────
    goal_amount = Decimal(data.funding_goal_cents) / Decimal(100)

    # ─────────────────────────────────────────────
    # Duration → end_date
    # ─────────────────────────────────────────────
    end_date = None
    if data.duration_days and data.duration_days > 0:
        end_date = datetime.now(timezone.utc) + timedelta(days=data.duration_days)

    # ─────────────────────────────────────────────
    # Create campaign
    # ─────────────────────────────────────────────
    campaign = Campaign(
        title=data.title,
        slug=slug,
        description=data.description_html,
        goal_amount=goal_amount,
        creator_id=user.id,  # from Clerk-authenticated user
        status=CampaignStatus.DRAFT,  # always draft
        category=data.category,
        location=data.location,
        end_date=end_date,
    )

    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)

    return build_campaign_response(
        campaign,
        creator_name=user.name,
    )


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
    if campaign.status in (CampaignStatus.FUNDED, CampaignStatus.CANCELLED, CampaignStatus.SUSPENDED):
        raise HTTPException(status_code=400, detail=f"Cannot edit a {campaign.status.value} campaign")

    if data.title is not None:
        campaign.title = data.title
    if data.short_description is not None:
        campaign.short_description = data.short_description
    if data.description is not None:
        campaign.description = data.description
    if data.image_url is not None:
        campaign.image_url = data.image_url
    if data.video_url is not None:
        campaign.video_url = data.video_url
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
    if campaign.status != CampaignStatus.DRAFT:
        raise HTTPException(status_code=400, detail="Only draft campaigns can be published")

    campaign.status = CampaignStatus.ACTIVE
    await db.flush()
    return build_campaign_response(campaign, creator_name=user.name)


# ── Cancel ─────────────────────────────────────────────────────────────────

@router.post("/{campaign_id}/cancel", response_model=CampaignResponse)
async def cancel_campaign(
    campaign_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a campaign. Only creator. Cannot cancel if already funded."""
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.creator_id != user.id:
        raise HTTPException(status_code=403, detail="Not your campaign")
    if campaign.status == CampaignStatus.FUNDED:
        raise HTTPException(status_code=400, detail="Cannot cancel a funded campaign")

    campaign.status = CampaignStatus.CANCELLED
    await db.flush()
    return build_campaign_response(campaign, creator_name=user.name)