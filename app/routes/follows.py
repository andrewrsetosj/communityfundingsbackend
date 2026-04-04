from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Optional

from app.database import get_db
from app.auth import get_current_user
from app.models.models import User

router = APIRouter(prefix="/api/follows", tags=["follows"])


# ─────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────
@router.get("/{creator_id}/summary")
async def get_follow_summary(creator_id: str, db: AsyncSession = Depends(get_db)):
    creator = await db.get(User, creator_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Profile not found")

    followers = await db.execute(
        text("SELECT COUNT(*) FROM creator_follows WHERE followed_creator_id = :id"),
        {"id": creator_id},
    )
    followers_count = followers.scalar() or 0

    following = await db.execute(
        text("SELECT COUNT(*) FROM creator_follows WHERE follower_creator_id = :id"),
        {"id": creator_id},
    )
    following_count = following.scalar() or 0

    return {
        "creator_id": creator_id,
        "followers_count": followers_count,
        "following_count": following_count,
    }


# ─────────────────────────────────────────
# RELATIONSHIP
# ─────────────────────────────────────────
@router.get("/{creator_id}/relationship")
async def get_follow_relationship(
    creator_id: str,
    current_user: Optional[User] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Missing Authorization")

    if current_user.id == creator_id:
        return {
            "viewer_creator_id": current_user.id,
            "is_self": True,
            "is_following": False,
            "follows_you": False,
            "is_friend": False,
        }

    creator = await db.get(User, creator_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Profile not found")

    is_following_result = await db.execute(
        text("""
            SELECT EXISTS(
                SELECT 1 FROM creator_follows
                WHERE follower_creator_id = :viewer
                  AND followed_creator_id = :target
            )
        """),
        {"viewer": current_user.id, "target": creator_id},
    )

    follows_you_result = await db.execute(
        text("""
            SELECT EXISTS(
                SELECT 1 FROM creator_follows
                WHERE follower_creator_id = :target
                  AND followed_creator_id = :viewer
            )
        """),
        {"viewer": current_user.id, "target": creator_id},
    )

    is_following_val = bool(is_following_result.scalar())
    follows_you_val = bool(follows_you_result.scalar())

    return {
        "viewer_creator_id": current_user.id,
        "is_self": False,
        "is_following": is_following_val,
        "follows_you": follows_you_val,
        "is_friend": is_following_val and follows_you_val,
    }


# ─────────────────────────────────────────
# FOLLOWERS LIST
# ─────────────────────────────────────────
@router.get("/{creator_id}/followers")
async def get_followers(
    creator_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    creator = await db.get(User, creator_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Profile not found")

    result = await db.execute(
        text("""
            SELECT
                c.creator_id,
                c.name,
                c.last_name,
                c.user_type,
                EXISTS(
                    SELECT 1
                    FROM creator_follows cf2
                    WHERE cf2.follower_creator_id = :viewer_id
                      AND cf2.followed_creator_id = c.creator_id
                ) AS viewer_follows_them,
                EXISTS(
                    SELECT 1
                    FROM creator_follows cf3
                    WHERE cf3.follower_creator_id = c.creator_id
                      AND cf3.followed_creator_id = :viewer_id
                ) AS they_follow_viewer
            FROM creator_follows cf
            JOIN creators c
              ON c.creator_id = cf.follower_creator_id
            WHERE cf.followed_creator_id = :creator_id
            ORDER BY c.name ASC, c.last_name ASC, c.creator_id ASC
        """),
        {"creator_id": creator_id, "viewer_id": current_user.id},
    )

    followers = []
    for row in result.mappings():
        viewer_follows_them = bool(row["viewer_follows_them"])
        they_follow_viewer = bool(row["they_follow_viewer"])

        followers.append({
            "creator_id": row["creator_id"],
            "name": row["name"],
            "last_name": row["last_name"],
            "user_type": row["user_type"],
            "follows_you": they_follow_viewer,
            "is_friend": viewer_follows_them and they_follow_viewer,
            "is_you": row["creator_id"] == current_user.id,
        })

    return {
        "creator_id": creator_id,
        "followers": followers,
    }


# ─────────────────────────────────────────
# FOLLOWING LIST
# ─────────────────────────────────────────
@router.get("/{creator_id}/following")
async def get_following(
    creator_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    creator = await db.get(User, creator_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Profile not found")

    result = await db.execute(
        text("""
            SELECT
                c.creator_id,
                c.name,
                c.last_name,
                c.user_type,
                EXISTS(
                    SELECT 1
                    FROM creator_follows cf2
                    WHERE cf2.follower_creator_id = :viewer_id
                      AND cf2.followed_creator_id = c.creator_id
                ) AS viewer_follows_them,
                EXISTS(
                    SELECT 1
                    FROM creator_follows cf3
                    WHERE cf3.follower_creator_id = c.creator_id
                      AND cf3.followed_creator_id = :viewer_id
                ) AS they_follow_viewer
            FROM creator_follows cf
            JOIN creators c
              ON c.creator_id = cf.followed_creator_id
            WHERE cf.follower_creator_id = :creator_id
            ORDER BY c.name ASC, c.last_name ASC, c.creator_id ASC
        """),
        {"creator_id": creator_id, "viewer_id": current_user.id},
    )

    following = []
    for row in result.mappings():
        viewer_follows_them = bool(row["viewer_follows_them"])
        they_follow_viewer = bool(row["they_follow_viewer"])

        following.append({
            "creator_id": row["creator_id"],
            "name": row["name"],
            "last_name": row["last_name"],
            "user_type": row["user_type"],
            "follows_you": they_follow_viewer,
            "is_friend": viewer_follows_them and they_follow_viewer,
            "is_you": row["creator_id"] == current_user.id,
        })

    return {
        "creator_id": creator_id,
        "following": following,
    }


# ─────────────────────────────────────────
# FOLLOW
# ─────────────────────────────────────────
@router.post("/{creator_id}")
async def follow_creator(
    creator_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.id == creator_id:
        raise HTTPException(status_code=400, detail="You cannot follow yourself")

    creator = await db.get(User, creator_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Profile not found")

    await db.execute(
        text("""
            INSERT INTO creator_follows (follower_creator_id, followed_creator_id)
            VALUES (:viewer, :target)
            ON CONFLICT DO NOTHING
        """),
        {"viewer": current_user.id, "target": creator_id},
    )
    await db.commit()

    return {"ok": True}


# ─────────────────────────────────────────
# UNFOLLOW
# ─────────────────────────────────────────
@router.delete("/{creator_id}")
async def unfollow_creator(
    creator_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        text("""
            DELETE FROM creator_follows
            WHERE follower_creator_id = :viewer
              AND followed_creator_id = :target
        """),
        {"viewer": current_user.id, "target": creator_id},
    )
    await db.commit()

    return {"ok": True}