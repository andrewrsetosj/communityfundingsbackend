"""
Comments — social proof on campaign pages
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func as sqlfunc
from sqlalchemy.orm import selectinload
from typing import List

from app.database import get_db
from app.auth import get_current_user
from app.models.models import Comment, User
from app.models.schemas import CommentCreate, CommentResponse

router = APIRouter(prefix="/api/campaigns/{campaign_id}/comments", tags=["comments"])


@router.get("", response_model=List[CommentResponse])
async def list_comments(
    campaign_id: str,
    limit: int = Query(default=20, le=100),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """Get comments for a campaign (public, excludes hidden)."""
    result = await db.execute(
        select(Comment).options(selectinload(Comment.user))
        .where(Comment.campaign_id == campaign_id, Comment.is_hidden == False)
        .order_by(Comment.created_at.desc())
        .offset(offset).limit(limit)
    )
    comments = result.scalars().all()
    return [
        CommentResponse(
            id=c.id, campaign_id=c.campaign_id, user_id=c.user_id,
            user_name=c.user.name if c.user else "Unknown",
            user_avatar=c.user.avatar_url if c.user else None,
            content=c.content, created_at=c.created_at,
        )
        for c in comments
    ]


@router.post("", response_model=CommentResponse, status_code=201)
async def create_comment(
    campaign_id: str,
    data: CommentCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Post a comment on a campaign. Must be logged in."""
    comment = Comment(
        campaign_id=campaign_id,
        user_id=user.id,
        content=data.content,
    )
    db.add(comment)
    await db.flush()

    return CommentResponse(
        id=comment.id, campaign_id=comment.campaign_id,
        user_id=comment.user_id,
        user_name=user.name, user_avatar=user.avatar_url,
        content=comment.content, created_at=comment.created_at,
    )


@router.delete("/{comment_id}")
async def delete_comment(
    campaign_id: str,
    comment_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete own comment."""
    result = await db.execute(
        select(Comment).where(Comment.id == comment_id, Comment.campaign_id == campaign_id)
    )
    comment = result.scalar_one_or_none()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    if comment.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your comment")
    await db.delete(comment)
    return {"deleted": True}