"""
Campaign Updates — creators post progress updates to backers
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List

from app.database import get_db
from app.auth import get_current_user
from app.models.models import Campaign, CampaignUpdate, User
from app.models.schemas import CampaignUpdateCreate, CampaignUpdateResponse

router = APIRouter(prefix="/api/campaigns/{campaign_id}/updates", tags=["campaign-updates"])


@router.get("", response_model=List[CampaignUpdateResponse])
async def list_updates(campaign_id: str, db: AsyncSession = Depends(get_db)):
    """Get all updates for a campaign (public)."""
    result = await db.execute(
        select(CampaignUpdate)
        .where(CampaignUpdate.campaign_id == campaign_id)
        .order_by(CampaignUpdate.created_at.desc())
    )
    return [
        CampaignUpdateResponse(
            id=u.id, campaign_id=u.campaign_id, author_id=u.author_id,
            title=u.title, content=u.content, created_at=u.created_at,
        )
        for u in result.scalars().all()
    ]


@router.post("", response_model=CampaignUpdateResponse, status_code=201)
async def create_update(
    campaign_id: str,
    data: CampaignUpdateCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Post an update to a campaign. Only the creator can post updates."""
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.creator_id != user.id:
        raise HTTPException(status_code=403, detail="Only the campaign creator can post updates")

    update = CampaignUpdate(
        campaign_id=campaign_id,
        author_id=user.id,
        title=data.title,
        content=data.content,
    )
    db.add(update)
    await db.flush()

    return CampaignUpdateResponse(
        id=update.id, campaign_id=update.campaign_id,
        author_id=update.author_id,
        title=update.title, content=update.content,
        created_at=update.created_at,
    )


@router.delete("/{update_id}")
async def delete_update(
    campaign_id: str,
    update_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a campaign update. Only the creator."""
    result = await db.execute(
        select(CampaignUpdate).where(
            CampaignUpdate.id == update_id, CampaignUpdate.campaign_id == campaign_id
        )
    )
    update = result.scalar_one_or_none()
    if not update:
        raise HTTPException(status_code=404, detail="Update not found")
    if update.author_id != user.id:
        raise HTTPException(status_code=403, detail="Not your update")
    await db.delete(update)
    return {"deleted": True}