from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from math import ceil

from app.db import get_pool
from app.database import get_db
from app.auth import get_current_user
from app.models.models import User

router = APIRouter(prefix="/api/campaign-page", tags=["campaign-page"])

COMMENTS_PER_PAGE = 10
INITIAL_REPLIES_LIMIT = 5
COMMENT_MAX_LENGTH = 1000


class CreateCommentRequest(BaseModel):
    comment_text: str = Field(..., min_length=1, max_length=COMMENT_MAX_LENGTH)
    parent_comment_id: int | None = None


async def _get_campaign_by_url_or_id(campaign_url: str):
    pool = await get_pool()

    async with pool.acquire() as conn:
        campaign = None

        if campaign_url.isdigit():
            campaign = await conn.fetchrow(
                """
                SELECT *
                FROM campaigns
                WHERE campaign_id = $1
                """,
                int(campaign_url),
            )

        if not campaign:
            campaign = await conn.fetchrow(
                """
                SELECT *
                FROM campaigns
                WHERE url = $1
                """,
                campaign_url,
            )

        if not campaign:
            return None

        return dict(campaign)


async def _get_friend_ids_for_user(conn, viewer_id: str | None) -> set[str]:
    if not viewer_id:
        return set()

    rows = await conn.fetch(
        """
        SELECT cf1.followed_creator_id AS friend_id
        FROM creator_follows cf1
        INNER JOIN creator_follows cf2
          ON cf2.follower_creator_id = cf1.followed_creator_id
         AND cf2.followed_creator_id = cf1.follower_creator_id
        WHERE cf1.follower_creator_id = $1
        """,
        viewer_id,
    )

    return {row["friend_id"] for row in rows}


def _decorate_comment(
    comment: dict,
    viewer_id: str | None,
    friend_ids: set[str],
    campaign_owner_id: str,
):
    comment["is_you"] = bool(viewer_id and comment["creator_id"] == viewer_id)
    comment["is_friend"] = bool(comment["creator_id"] in friend_ids) if viewer_id else False
    comment["is_project_owner"] = comment["creator_id"] == campaign_owner_id
    return comment


@router.get("/{campaign_url}")
async def get_campaign_page(
    campaign_url: str,
    page: int = Query(1, ge=1),
    current_user: User | None = Depends(get_current_user),
):
    pool = await get_pool()

    async with pool.acquire() as conn:
        campaign = None

        if campaign_url.isdigit():
            campaign = await conn.fetchrow(
                """
                SELECT *
                FROM campaigns
                WHERE campaign_id = $1
                """,
                int(campaign_url),
            )

        if not campaign:
            campaign = await conn.fetchrow(
                """
                SELECT *
                FROM campaigns
                WHERE url = $1
                """,
                campaign_url,
            )

        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")

        campaign = dict(campaign)
        cid = campaign["campaign_id"]
        campaign_owner_id = campaign["creator_id"]
        viewer_id = current_user.id if current_user else None

        creator = await conn.fetchrow(
            """
            SELECT creator_id, name, last_name, email, bio, avatar_url
            FROM creators
            WHERE creator_id = $1
            """,
            campaign_owner_id,
        )
        creator = dict(creator) if creator else None

        faqs = await conn.fetch(
            """
            SELECT *
            FROM faqs
            WHERE campaign_id = $1
            ORDER BY display_order ASC
            """,
            cid,
        )
        faqs = [dict(f) for f in faqs]

        rewards = await conn.fetch(
            """
            SELECT *
            FROM rewards
            WHERE campaign_id = $1
            ORDER BY display_order ASC, reward_id ASC
            """,
            cid,
        )
        rewards = [dict(r) for r in rewards]

        photos = await conn.fetch(
            """
            SELECT *
            FROM campaign_photos
            WHERE campaign_id = $1
            ORDER BY is_primary DESC, photo_id ASC
            """,
            cid,
        )
        photos = [dict(p) for p in photos]

        for p in photos:
            p["image_url"] = (
                f"https://{p['s3_bucket']}.s3.us-east-2.amazonaws.com/"
                f"{p['s3_key']}"
            )

        friend_ids = await _get_friend_ids_for_user(conn, viewer_id)

        total_parent_comments = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM comments
            WHERE campaign_id = $1
              AND parent_comment_id IS NULL
            """,
            cid,
        )

        total_pages = max(1, ceil(total_parent_comments / COMMENTS_PER_PAGE))
        current_page = min(page, total_pages)
        offset = (current_page - 1) * COMMENTS_PER_PAGE

        parent_comments = await conn.fetch(
            """
            SELECT
                c.comment_id,
                c.comment_text,
                c.creator_id,
                c.campaign_id,
                c.parent_comment_id,
                c.time_created,
                cr.name,
                cr.last_name,
                cr.avatar_url
            FROM comments c
            LEFT JOIN creators cr ON cr.creator_id = c.creator_id
            WHERE c.campaign_id = $1
              AND c.parent_comment_id IS NULL
            ORDER BY c.time_created DESC, c.comment_id DESC
            LIMIT $2 OFFSET $3
            """,
            cid,
            COMMENTS_PER_PAGE,
            offset,
        )

        parent_comments = [dict(c) for c in parent_comments]
        parent_comments = [
            _decorate_comment(c, viewer_id, friend_ids, campaign_owner_id)
            for c in parent_comments
        ]

        comment_ids = [c["comment_id"] for c in parent_comments]

        replies_by_parent: dict[int, list[dict]] = {}
        reply_counts_by_parent: dict[int, int] = {}

        if comment_ids:
            reply_counts = await conn.fetch(
                """
                SELECT parent_comment_id, COUNT(*) AS reply_count
                FROM comments
                WHERE parent_comment_id = ANY($1::int[])
                GROUP BY parent_comment_id
                """,
                comment_ids,
            )

            reply_counts_by_parent = {
                row["parent_comment_id"]: row["reply_count"] for row in reply_counts
            }

            initial_replies = await conn.fetch(
                """
                SELECT *
                FROM (
                    SELECT
                        c.comment_id,
                        c.comment_text,
                        c.creator_id,
                        c.campaign_id,
                        c.parent_comment_id,
                        c.time_created,
                        cr.name,
                        cr.last_name,
                        cr.avatar_url,
                        ROW_NUMBER() OVER (
                            PARTITION BY c.parent_comment_id
                            ORDER BY c.time_created ASC, c.comment_id ASC
                        ) AS rn
                    FROM comments c
                    LEFT JOIN creators cr ON cr.creator_id = c.creator_id
                    WHERE c.parent_comment_id = ANY($1::int[])
                ) ranked
                WHERE rn <= $2
                ORDER BY parent_comment_id ASC, time_created ASC, comment_id ASC
                """,
                comment_ids,
                INITIAL_REPLIES_LIMIT,
            )

            for row in initial_replies:
                reply = dict(row)
                reply.pop("rn", None)
                reply = _decorate_comment(reply, viewer_id, friend_ids, campaign_owner_id)
                parent_id = reply["parent_comment_id"]
                replies_by_parent.setdefault(parent_id, []).append(reply)

        comments = []
        for comment in parent_comments:
            replies = replies_by_parent.get(comment["comment_id"], [])
            reply_count = int(reply_counts_by_parent.get(comment["comment_id"], 0))

            comments.append({
                **comment,
                "replies": replies,
                "reply_count": reply_count,
                "has_more_replies": reply_count > len(replies),
            })

    return {
        "campaign": campaign,
        "creator": creator,
        "faqs": faqs,
        "rewards": rewards,
        "photos": photos,
        "comments": comments,
        "comments_pagination": {
            "page": current_page,
            "per_page": COMMENTS_PER_PAGE,
            "total_parent_comments": total_parent_comments,
            "total_pages": total_pages,
        },
    }


@router.get("/{campaign_url}/comments/{comment_id}/replies")
async def get_comment_replies(
    campaign_url: str,
    comment_id: int,
    current_user: User | None = Depends(get_current_user),
):
    campaign = await _get_campaign_by_url_or_id(campaign_url)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    pool = await get_pool()

    async with pool.acquire() as conn:
        parent_comment = await conn.fetchrow(
            """
            SELECT comment_id, campaign_id, parent_comment_id
            FROM comments
            WHERE comment_id = $1
            """,
            comment_id,
        )

        if not parent_comment:
            raise HTTPException(status_code=404, detail="Comment not found")

        if parent_comment["campaign_id"] != campaign["campaign_id"]:
            raise HTTPException(status_code=400, detail="Comment does not belong to this campaign")

        if parent_comment["parent_comment_id"] is not None:
            raise HTTPException(status_code=400, detail="Replies can only be loaded for parent comments")

        viewer_id = current_user.id if current_user else None
        friend_ids = await _get_friend_ids_for_user(conn, viewer_id)
        campaign_owner_id = campaign["creator_id"]

        replies = await conn.fetch(
            """
            SELECT
                c.comment_id,
                c.comment_text,
                c.creator_id,
                c.campaign_id,
                c.parent_comment_id,
                c.time_created,
                cr.name,
                cr.last_name,
                cr.avatar_url
            FROM comments c
            LEFT JOIN creators cr ON cr.creator_id = c.creator_id
            WHERE c.parent_comment_id = $1
            ORDER BY c.time_created ASC, c.comment_id ASC
            """,
            comment_id,
        )

        replies = [
            _decorate_comment(dict(r), viewer_id, friend_ids, campaign_owner_id)
            for r in replies
        ]

    return {
        "comment_id": comment_id,
        "replies": replies,
    }


@router.post("/{campaign_url}/comments")
async def create_comment(
    campaign_url: str,
    payload: CreateCommentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    campaign = await _get_campaign_by_url_or_id(campaign_url)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    comment_text = payload.comment_text.strip()
    if not comment_text:
        raise HTTPException(status_code=400, detail="Comment cannot be empty")

    if len(comment_text) > COMMENT_MAX_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Comment cannot exceed {COMMENT_MAX_LENGTH} characters",
        )

    pool = await get_pool()

    async with pool.acquire() as conn:
        if payload.parent_comment_id is not None:
            parent_comment = await conn.fetchrow(
                """
                SELECT comment_id, campaign_id
                FROM comments
                WHERE comment_id = $1
                """,
                payload.parent_comment_id,
            )

            if not parent_comment:
                raise HTTPException(status_code=404, detail="Parent comment not found")

            if parent_comment["campaign_id"] != campaign["campaign_id"]:
                raise HTTPException(status_code=400, detail="Parent comment does not belong to this campaign")

        inserted = await conn.fetchrow(
            """
            INSERT INTO comments (comment_text, creator_id, campaign_id, parent_comment_id)
            VALUES ($1, $2, $3, $4)
            RETURNING comment_id, comment_text, creator_id, campaign_id, parent_comment_id, time_created
            """,
            comment_text,
            current_user.id,
            campaign["campaign_id"],
            payload.parent_comment_id,
        )

        inserted = dict(inserted)

        creator_row = await conn.fetchrow(
            """
            SELECT name, last_name, avatar_url
            FROM creators
            WHERE creator_id = $1
            """,
            current_user.id,
        )

        friend_ids = await _get_friend_ids_for_user(conn, current_user.id)

    comment = {
        **inserted,
        "name": creator_row["name"] if creator_row else None,
        "last_name": creator_row["last_name"] if creator_row else None,
        "avatar_url": creator_row["avatar_url"] if creator_row else None,
        "replies": [],
        "reply_count": 0,
        "has_more_replies": False,
        "is_you": True,
        "is_friend": inserted["creator_id"] in friend_ids,
        "is_project_owner": inserted["creator_id"] == campaign["creator_id"],
    }

    return {
        "comment": comment
    }


@router.delete("/{campaign_url}/comments/{comment_id}")
async def delete_comment(
    campaign_url: str,
    comment_id: int,
    current_user: User = Depends(get_current_user),
):
    campaign = await _get_campaign_by_url_or_id(campaign_url)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    pool = await get_pool()

    async with pool.acquire() as conn:
        comment = await conn.fetchrow(
            """
            SELECT comment_id, creator_id, campaign_id
            FROM comments
            WHERE comment_id = $1
            """,
            comment_id,
        )

        if not comment:
            raise HTTPException(status_code=404, detail="Comment not found")

        if comment["campaign_id"] != campaign["campaign_id"]:
            raise HTTPException(status_code=400, detail="Comment does not belong to this campaign")

        if comment["creator_id"] != current_user.id:
            raise HTTPException(status_code=403, detail="You can only delete your own comments")

        await conn.execute(
            """
            DELETE FROM comments
            WHERE comment_id = $1
            """,
            comment_id,
        )

    return {
        "ok": True,
        "comment_id": comment_id,
    }