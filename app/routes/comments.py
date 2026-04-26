"""
Comments — social proof on campaign pages

Endpoints:
  GET    /api/campaigns/{campaign_id}/comments          — list (public, paginated, excludes hidden)
  POST   /api/campaigns/{campaign_id}/comments          — create (auth required)
  DELETE /api/campaigns/{campaign_id}/comments/{id}      — delete own comment (auth required)
  GET    /api/campaigns/{campaign_id}/comments/count     — total visible comment count (public)
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func as sqlfunc
from sqlalchemy.orm import selectinload
from typing import List

from app.database import get_db
from app.auth import get_current_user
from app.models.models import Comment, Campaign, User
from app.models.schemas import CommentCreate, CommentResponse

router = APIRouter(prefix="/api/campaigns/{campaign_id}/comments", tags=["comments"])


# ── Helpers ────────────────────────────────────────────────────────────────

async def _get_campaign_or_404(campaign_id: str, db: AsyncSession) -> Campaign:
    """Fetch campaign by ID; raise 404 if not found."""
    result = await db.execute(select(Campaign).where(Campaign.id == int(campaign_id)))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


# ── List comments (public) ─────────────────────────────────────────────────

@router.get("", response_model=List[CommentResponse])
async def list_comments(
    campaign_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Get comments for a campaign (public, excludes hidden)."""
    result = await db.execute(
        select(Comment)
        .options(selectinload(Comment.user))
        .where(Comment.campaign_id == int(campaign_id), Comment.is_hidden == False)
        .order_by(Comment.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    comments = result.scalars().all()
    return [
        CommentResponse(
            id=c.id,
            campaign_id=c.campaign_id,
            creator_id=c.user_id,
            user_name=c.user.name if c.user else "Unknown",
            user_avatar=c.user.avatar_url if c.user else None,
            comment_text=c.content,
            created_at=c.created_at,
        )
        for c in comments
    ]


# ── Comment count (public) ─────────────────────────────────────────────────

@router.get("/count")
async def comment_count(
    campaign_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get total visible comment count for a campaign."""
    result = await db.execute(
        select(sqlfunc.count(Comment.id)).where(
            Comment.campaign_id == int(campaign_id),
            Comment.is_hidden == False,
        )
    )
    total = result.scalar() or 0
    return {"campaign_id": campaign_id, "count": total}


# ── Create comment (auth required) ────────────────────────────────────────

@router.post("", response_model=CommentResponse, status_code=201)
async def create_comment(
    campaign_id: str,
    data: CommentCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Post a comment on a campaign. Must be logged in."""
    # Validate campaign exists
    await _get_campaign_or_404(campaign_id, db)

    comment = Comment(
        campaign_id=int(campaign_id),
        user_id=user.id,
        content=data.content,
    )
    db.add(comment)
    await db.flush()
    await db.commit()
    return CommentResponse(
        id=comment.id,
        campaign_id=comment.campaign_id,
        creator_id=comment.user_id,
        user_name=user.name,
        user_avatar=user.avatar_url,
        comment_text=comment.content,
        created_at=comment.created_at,
    )


# ── Delete comment (auth required, own comment only) ──────────────────────

@router.delete("/{comment_id}")
async def delete_comment(
    campaign_id: str,
    comment_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete own comment."""
    result = await db.execute(
        select(Comment).where(
            Comment.id == comment_id,
            Comment.campaign_id == int(campaign_id),
        )
    )
    comment = result.scalar_one_or_none()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    if comment.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your comment")
    await db.delete(comment)
    await db.commit()
    return {"deleted": True}