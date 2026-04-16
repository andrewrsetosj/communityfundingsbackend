from datetime import timedelta
from math import ceil
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth import get_current_user
from app.db import get_pool
from app.models.models import User

router = APIRouter(prefix="/api/campaign-page", tags=["campaign-page"])

COMMENTS_PER_PAGE = 10
INITIAL_REPLIES_LIMIT = 5
COMMENT_MAX_LENGTH = 1000
SORT_BY_VALUES = {"newest", "oldest", "most_liked"}
MAX_DURATION_DAYS = 365
CATEGORY_OPTIONS = {
    "Art",
    "Comics",
    "Crafts",
    "Dance",
    "Design",
    "Fashion",
    "Film & Video",
    "Food",
    "Games",
    "Journalism",
    "Music",
    "Photography",
    "Publishing",
    "Technology",
    "Theater",
}

BANNED_WORD_PATTERNS = [
    r"\bfuck(?:ing|er|ed|s)?\b",
    r"\bshit(?:ty|s)?\b",
    r"\bbitch(?:es)?\b",
    r"\basshole(?:s)?\b",
    r"\bdamn\b",
]


class CreateCommentRequest(BaseModel):
    comment_text: str = Field(..., min_length=1, max_length=COMMENT_MAX_LENGTH)
    parent_comment_id: int | None = None
    reply_to_comment_id: int | None = None


class UpdateCommentRequest(BaseModel):
    comment_text: str = Field(..., min_length=1, max_length=COMMENT_MAX_LENGTH)


class ReportCommentRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=2000)


class ReportCampaignRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=2000)


class EditRewardRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    required_amount_cents: int = Field(..., ge=1)
    limit_total: int | None = Field(default=None, ge=1)
    display_order: int = Field(default=0, ge=0)


class EditFaqRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    answer: str = Field(..., min_length=1, max_length=4000)
    display_order: int = Field(default=0, ge=0)


class EditPhotoRequest(BaseModel):
    s3_bucket: str = Field(..., min_length=1, max_length=255)
    s3_key: str = Field(..., min_length=1, max_length=1024)
    content_type: str = Field(default="image/jpeg", min_length=1, max_length=255)
    is_primary: bool = False
    sort_order: int = Field(default=0, ge=0)


class EditCampaignRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description_html: str = Field(..., min_length=1)
    category: str | None = None
    location: str | None = None
    funding_goal_cents: int = Field(..., ge=1)
    duration_days: int | None = Field(default=None, ge=1, le=MAX_DURATION_DAYS)
    rewards: list[EditRewardRequest] = Field(default_factory=list)
    faqs: list[EditFaqRequest] = Field(default_factory=list)
    photos: list[EditPhotoRequest] = Field(default_factory=list)


async def _get_campaign_collaborators(conn, campaign_id: int) -> list[dict]:
    collaborators = await conn.fetch(
        """
        SELECT
            coll.collaborator_id,
            coll.campaign_id,
            coll.email,
            coll.status,
            coll.time_created,
            cr.creator_id,
            COALESCE(NULLIF(cr.username, ''), cr.creator_id) AS username,
            cr.name,
            cr.last_name,
            cr.avatar_url,
            cr.bio,
            cr.user_type
        FROM collaborators coll
        JOIN creators cr
          ON LOWER(cr.email) = LOWER(coll.email)
        WHERE coll.campaign_id = $1
          AND LOWER(COALESCE(coll.status, '')) = 'accepted'
        ORDER BY coll.time_created ASC, coll.collaborator_id ASC
        """,
        campaign_id,
    )
    return [dict(row) for row in collaborators]


async def _get_campaign_by_url_or_id(campaign_url: str):
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE campaigns
            SET status = 'inactive'
            WHERE status = 'active'
            AND end_date IS NOT NULL
            AND end_date <= NOW()
            """
        )
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

        return dict(campaign) if campaign else None


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


async def _get_viewer_collaborator_status(conn, campaign_id: int, viewer_id: str | None) -> tuple[bool, bool]:
    if not viewer_id:
        return False, False

    viewer = await conn.fetchrow(
        """
        SELECT email
        FROM creators
        WHERE creator_id = $1
        LIMIT 1
        """,
        viewer_id,
    )
    viewer_email = ((viewer["email"] if viewer else None) or "").strip()
    if not viewer_email:
        return False, False

    invite = await conn.fetchrow(
        """
        SELECT LOWER(COALESCE(status, 'pending')) AS status
        FROM collaborators
        WHERE campaign_id = $1
          AND LOWER(email) = LOWER($2)
        ORDER BY collaborator_id DESC
        LIMIT 1
        """,
        campaign_id,
        viewer_email,
    )
    if not invite:
        return False, False

    status = (invite["status"] or "").lower()
    return status == "accepted", status == "pending"

async def _get_viewer_saved_status(conn, campaign_id: int, viewer_id: str | None) -> bool:
    if not viewer_id:
        return False

    saved = await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1
            FROM saved_campaigns
            WHERE creator_id = $1
              AND campaign_id = $2
        )
        """,
        viewer_id,
        campaign_id,
    )
    return bool(saved)

def _contains_foul_language(text: str) -> bool:
    normalized = re.sub(r"[^a-zA-Z0-9\s]", " ", text.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in BANNED_WORD_PATTERNS)


def _validate_campaign_edit_payload(payload: EditCampaignRequest, existing_campaign: dict | None = None) -> None:
    title = payload.title.strip()
    description_html = payload.description_html.strip()
    category = (payload.category or "").strip()
    location = (payload.location or "").strip()

    if not title:
        raise HTTPException(status_code=400, detail="Project title is required")
    if not description_html:
        raise HTTPException(status_code=400, detail="Story / description is required")
    if not category:
        raise HTTPException(status_code=400, detail="Category is required")
    if category not in CATEGORY_OPTIONS:
        raise HTTPException(status_code=400, detail="Please choose a valid category")
    if not location:
        raise HTTPException(status_code=400, detail="Location is required")
    if payload.funding_goal_cents < 1:
        raise HTTPException(status_code=400, detail="Funding goal must be greater than 0")
    if payload.duration_days is None:
        raise HTTPException(status_code=400, detail="Duration is required")
    if payload.duration_days < 1 or payload.duration_days > MAX_DURATION_DAYS:
        raise HTTPException(status_code=400, detail=f"Duration must be between 1 and {MAX_DURATION_DAYS} days")

    if existing_campaign and int(existing_campaign.get("backers") or 0) > 0:
        locked_changes = []
        if title != (existing_campaign.get("title") or "").strip():
            locked_changes.append("title")
        if category != (existing_campaign.get("category") or "").strip():
            locked_changes.append("category")
        if location != (existing_campaign.get("location") or "").strip():
            locked_changes.append("location")
        if int(payload.funding_goal_cents) != int(existing_campaign.get("funding_goal_cents") or 0):
            locked_changes.append("funding goal")
        existing_duration = existing_campaign.get("duration_days")
        if payload.duration_days != existing_duration:
            locked_changes.append("duration")

        if locked_changes:
            raise HTTPException(
                status_code=400,
                detail="Because backers have contributed to your campaign, you can no longer edit the title, category, location, funding goal, or duration.",
            )


def _validate_comment_text(comment_text: str) -> str:
    cleaned = comment_text.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Comment cannot be empty")
    if len(cleaned) > COMMENT_MAX_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Comment cannot exceed {COMMENT_MAX_LENGTH} characters",
        )
    if _contains_foul_language(cleaned):
        raise HTTPException(status_code=400, detail="Please remove profanity from your comment and try again")
    return cleaned


def _parent_order_sql(sort_by: str) -> str:
    if sort_by == "oldest":
        return "c.time_created ASC, c.comment_id ASC"
    if sort_by == "most_liked":
        return "like_count DESC, c.time_created DESC, c.comment_id DESC"
    return "c.time_created DESC, c.comment_id DESC"


def _decorate_comment(
    comment: dict,
    viewer_id: str | None,
    friend_ids: set[str],
    campaign_owner_id: str,
    collaborator_ids: set[str],
):
    comment["is_you"] = bool(viewer_id and comment["creator_id"] == viewer_id)
    comment["is_friend"] = bool(comment["creator_id"] in friend_ids) if viewer_id else False
    comment["is_project_owner"] = comment["creator_id"] == campaign_owner_id
    comment["is_project_collaborator"] = (
        comment["creator_id"] in collaborator_ids and not comment["is_project_owner"]
    )
    comment["like_count"] = int(comment.get("like_count") or 0)
    comment["liked_by_viewer"] = bool(comment.get("liked_by_viewer"))

    time_created = comment.get("time_created")
    updated_at = comment.get("updated_at")
    comment["was_edited"] = bool(
        time_created
        and updated_at
        and updated_at - time_created > timedelta(seconds=5)
    )

    return comment


async def _fetch_comment_payload(
    conn,
    comment_id: int,
    viewer_id: str | None,
    campaign_owner_id: str,
    friend_ids: set[str],
    collaborator_ids: set[str],
) -> dict:
    row = await conn.fetchrow(
        """
        SELECT
            c.comment_id,
            c.comment_text,
            c.creator_id,
            c.campaign_id,
            c.parent_comment_id,
            c.reply_to_comment_id,
            c.time_created,
            c.updated_at,
            COALESCE(NULLIF(cr.username, ''), cr.creator_id) AS username,
            cr.name,
            cr.last_name,
            cr.avatar_url,
            cr.user_type,
            rt.name AS reply_to_name,
            COALESCE(cl.like_count, 0) AS like_count,
            CASE
              WHEN $2::text IS NULL THEN FALSE
              ELSE EXISTS (
                SELECT 1
                FROM comment_likes viewer_like
                WHERE viewer_like.comment_id = c.comment_id
                  AND viewer_like.creator_id = $2
              )
            END AS liked_by_viewer
        FROM comments c
        LEFT JOIN creators cr ON cr.creator_id = c.creator_id
        LEFT JOIN creators rt ON rt.creator_id = (
            SELECT creator_id FROM comments WHERE comment_id = c.reply_to_comment_id
        )
        LEFT JOIN (
            SELECT comment_id, COUNT(*) AS like_count
            FROM comment_likes
            GROUP BY comment_id
        ) cl ON cl.comment_id = c.comment_id
        WHERE c.comment_id = $1
        """,
        comment_id,
        viewer_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Comment not found")

    comment = dict(row)
    comment = _decorate_comment(comment, viewer_id, friend_ids, campaign_owner_id, collaborator_ids)
    comment["replies"] = []
    comment["reply_count"] = 0
    comment["has_more_replies"] = False
    return comment


@router.get("/{campaign_url}")
async def get_campaign_page(
    campaign_url: str,
    page: int = Query(1, ge=1),
    sort_by: Literal["newest", "oldest", "most_liked"] = Query("newest"),
    current_user: User | None = Depends(get_current_user),
):
    campaign = await _get_campaign_by_url_or_id(campaign_url)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    pool = await get_pool()

    async with pool.acquire() as conn:
        cid = campaign["campaign_id"]
        campaign_owner_id = campaign["creator_id"]
        viewer_id = current_user.id if current_user else None

        creator = await conn.fetchrow(
            """
            SELECT creator_id, COALESCE(NULLIF(username, ''), creator_id) AS username, name, last_name, email, bio, avatar_url, user_type
            FROM creators
            WHERE creator_id = $1
            """,
            campaign_owner_id,
        )
        creator = dict(creator) if creator else None
        collaborators = await _get_campaign_collaborators(conn, cid)
        collaborator_ids = {row["creator_id"] for row in collaborators if row.get("creator_id")}

        is_owner = bool(viewer_id and viewer_id == campaign_owner_id)
        is_collaborator, has_pending_invite = await _get_viewer_collaborator_status(conn, cid, viewer_id)
        is_saved = await _get_viewer_saved_status(conn, cid, viewer_id)

        is_public_campaign = campaign.get("status") in {"active", "inactive"}

        can_view_campaign = bool(
            is_public_campaign
            or is_owner
            or is_collaborator
            or has_pending_invite
        )
        can_comment = bool(campaign.get("status") == "active" and can_view_campaign)

        if not can_view_campaign:
            return {
                "campaign": campaign,
                "creator": creator,
                "collaborators": [],
                "faqs": [],
                "rewards": [],
                "photos": [],
                "comments": [],
                "comments_pagination": {
                    "page": 1,
                    "per_page": COMMENTS_PER_PAGE,
                    "total_parent_comments": 0,
                    "total_pages": 1,
                },
                "viewer_permissions": {
                    "is_owner": is_owner,
                    "is_collaborator": is_collaborator,
                    "has_pending_invite": has_pending_invite,
                    "can_view": False,
                    "can_comment": False,
                },
                "viewer_engagement": {
                    "is_saved": is_saved,
                },
            }
        faqs = [
            dict(f)
            for f in await conn.fetch(
                """
                SELECT *
                FROM faqs
                WHERE campaign_id = $1
                ORDER BY display_order ASC
                """,
                cid,
            )
        ]

        rewards = [
            dict(r)
            for r in await conn.fetch(
                """
                SELECT *
                FROM rewards
                WHERE campaign_id = $1
                ORDER BY display_order ASC, reward_id ASC
                """,
                cid,
            )
        ]

        photos = [
            dict(p)
            for p in await conn.fetch(
                """
                SELECT *
                FROM campaign_photos
                WHERE campaign_id = $1
                ORDER BY is_primary DESC, photo_id ASC
                """,
                cid,
            )
        ]
        for p in photos:
            p["image_url"] = f"https://{p['s3_bucket']}.s3.us-east-2.amazonaws.com/{p['s3_key']}"

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
        order_sql = _parent_order_sql(sort_by)

        parent_comments = await conn.fetch(
            f"""
            SELECT
                c.comment_id,
                c.comment_text,
                c.creator_id,
                c.campaign_id,
                c.parent_comment_id,
                c.reply_to_comment_id,
                c.time_created,
                c.updated_at,
                COALESCE(NULLIF(cr.username, ''), cr.creator_id) AS username,
                cr.name,
                cr.last_name,
                cr.avatar_url,
                cr.user_type,
                rt.name AS reply_to_name,
                COALESCE(cl.like_count, 0) AS like_count,
                CASE
                  WHEN $4::text IS NULL THEN FALSE
                  ELSE EXISTS (
                    SELECT 1
                    FROM comment_likes viewer_like
                    WHERE viewer_like.comment_id = c.comment_id
                      AND viewer_like.creator_id = $4
                  )
                END AS liked_by_viewer
            FROM comments c
            LEFT JOIN creators cr ON cr.creator_id = c.creator_id
            LEFT JOIN creators rt ON rt.creator_id = (
                SELECT creator_id FROM comments WHERE comment_id = c.reply_to_comment_id
            )
            LEFT JOIN (
                SELECT comment_id, COUNT(*) AS like_count
                FROM comment_likes
                GROUP BY comment_id
            ) cl ON cl.comment_id = c.comment_id
            WHERE c.campaign_id = $1
              AND c.parent_comment_id IS NULL
            ORDER BY {order_sql}
            LIMIT $2 OFFSET $3
            """,
            cid,
            COMMENTS_PER_PAGE,
            offset,
            viewer_id,
        )

        parent_comments = [
            _decorate_comment(dict(c), viewer_id, friend_ids, campaign_owner_id, collaborator_ids)
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
                row["parent_comment_id"]: int(row["reply_count"])
                for row in reply_counts
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
                        c.reply_to_comment_id,
                        c.time_created,
                        c.updated_at,
                        COALESCE(NULLIF(cr.username, ''), cr.creator_id) AS username,
                        cr.name,
                        cr.last_name,
                        cr.avatar_url,
                        cr.user_type,
                        rt.name AS reply_to_name,
                        COALESCE(cl.like_count, 0) AS like_count,
                        CASE
                          WHEN $3::text IS NULL THEN FALSE
                          ELSE EXISTS (
                            SELECT 1
                            FROM comment_likes viewer_like
                            WHERE viewer_like.comment_id = c.comment_id
                              AND viewer_like.creator_id = $3
                          )
                        END AS liked_by_viewer,
                        ROW_NUMBER() OVER (
                            PARTITION BY c.parent_comment_id
                            ORDER BY c.time_created ASC, c.comment_id ASC
                        ) AS rn
                    FROM comments c
                    LEFT JOIN creators cr ON cr.creator_id = c.creator_id
                    LEFT JOIN creators rt ON rt.creator_id = (
                        SELECT creator_id FROM comments WHERE comment_id = c.reply_to_comment_id
                    )
                    LEFT JOIN (
                        SELECT comment_id, COUNT(*) AS like_count
                        FROM comment_likes
                        GROUP BY comment_id
                    ) cl ON cl.comment_id = c.comment_id
                    WHERE c.parent_comment_id = ANY($1::int[])
                ) ranked
                WHERE rn <= $2
                ORDER BY parent_comment_id ASC, time_created ASC, comment_id ASC
                """,
                comment_ids,
                INITIAL_REPLIES_LIMIT,
                viewer_id,
            )

            for row in initial_replies:
                reply = dict(row)
                reply.pop("rn", None)
                reply = _decorate_comment(reply, viewer_id, friend_ids, campaign_owner_id, collaborator_ids)
                replies_by_parent.setdefault(reply["parent_comment_id"], []).append(reply)

        comments = []
        for comment in parent_comments:
            replies = replies_by_parent.get(comment["comment_id"], [])
            reply_count = int(reply_counts_by_parent.get(comment["comment_id"], 0))
            comments.append(
                {
                    **comment,
                    "replies": replies,
                    "reply_count": reply_count,
                    "has_more_replies": reply_count > len(replies),
                }
            )

    return {
        "campaign": campaign,
        "creator": creator,
        "collaborators": collaborators,
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
        "viewer_permissions": {
            "is_owner": is_owner,
            "is_collaborator": is_collaborator,
            "has_pending_invite": has_pending_invite,
            "can_view": can_view_campaign,
            "can_comment": can_comment,
        },
        "viewer_engagement": {
            "is_saved": is_saved,
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
        collaborators = await _get_campaign_collaborators(conn, campaign["campaign_id"])
        collaborator_ids = {row["creator_id"] for row in collaborators if row.get("creator_id")}

        replies = await conn.fetch(
            """
            SELECT
                c.comment_id,
                c.comment_text,
                c.creator_id,
                c.campaign_id,
                c.parent_comment_id,
                c.reply_to_comment_id,
                c.time_created,
                c.updated_at,
                COALESCE(NULLIF(cr.username, ''), cr.creator_id) AS username,
                cr.name,
                cr.last_name,
                cr.avatar_url,
                cr.user_type,
                rt.name AS reply_to_name,
                COALESCE(cl.like_count, 0) AS like_count,
                CASE
                  WHEN $2::text IS NULL THEN FALSE
                  ELSE EXISTS (
                    SELECT 1
                    FROM comment_likes viewer_like
                    WHERE viewer_like.comment_id = c.comment_id
                      AND viewer_like.creator_id = $2
                  )
                END AS liked_by_viewer
            FROM comments c
            LEFT JOIN creators cr ON cr.creator_id = c.creator_id
            LEFT JOIN creators rt ON rt.creator_id = (
                SELECT creator_id FROM comments WHERE comment_id = c.reply_to_comment_id
            )
            LEFT JOIN (
                SELECT comment_id, COUNT(*) AS like_count
                FROM comment_likes
                GROUP BY comment_id
            ) cl ON cl.comment_id = c.comment_id
            WHERE c.parent_comment_id = $1
            ORDER BY c.time_created ASC, c.comment_id ASC
            """,
            comment_id,
            viewer_id,
        )

        replies = [
            _decorate_comment(dict(r), viewer_id, friend_ids, campaign_owner_id, collaborator_ids)
            for r in replies
        ]

    return {"comment_id": comment_id, "replies": replies}


@router.post("/{campaign_url}/comments")
async def create_comment(
    campaign_url: str,
    payload: CreateCommentRequest,
    current_user: User = Depends(get_current_user),
):
    campaign = await _get_campaign_by_url_or_id(campaign_url)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    comment_text = _validate_comment_text(payload.comment_text)
    pool = await get_pool()

    async with pool.acquire() as conn:
        parent_comment_id = payload.parent_comment_id
        reply_to_comment_id = payload.reply_to_comment_id

        if parent_comment_id is not None:
            parent_comment = await conn.fetchrow(
                """
                SELECT comment_id, campaign_id, parent_comment_id
                FROM comments
                WHERE comment_id = $1
                """,
                parent_comment_id,
            )
            if not parent_comment:
                raise HTTPException(status_code=404, detail="Parent comment not found")
            if parent_comment["campaign_id"] != campaign["campaign_id"]:
                raise HTTPException(status_code=400, detail="Parent comment does not belong to this campaign")
            if parent_comment["parent_comment_id"] is not None:
                raise HTTPException(status_code=400, detail="Replies must attach to a top-level comment")

        if reply_to_comment_id is not None:
            reply_target = await conn.fetchrow(
                """
                SELECT comment_id, campaign_id, parent_comment_id
                FROM comments
                WHERE comment_id = $1
                """,
                reply_to_comment_id,
            )
            if not reply_target:
                raise HTTPException(status_code=404, detail="Reply target not found")
            if reply_target["campaign_id"] != campaign["campaign_id"]:
                raise HTTPException(status_code=400, detail="Reply target does not belong to this campaign")

            inferred_parent = reply_target["parent_comment_id"] or reply_target["comment_id"]
            if parent_comment_id is None:
                parent_comment_id = inferred_parent
            elif parent_comment_id != inferred_parent:
                raise HTTPException(status_code=400, detail="Reply target must belong to the selected parent thread")

        inserted = await conn.fetchrow(
            """
            INSERT INTO comments (comment_text, creator_id, campaign_id, parent_comment_id, reply_to_comment_id, updated_at)
            VALUES ($1, $2, $3, $4, $5, NOW())
            RETURNING comment_id
            """,
            comment_text,
            current_user.id,
            campaign["campaign_id"],
            parent_comment_id,
            reply_to_comment_id,
        )

        friend_ids = await _get_friend_ids_for_user(conn, current_user.id)
        collaborators = await _get_campaign_collaborators(conn, campaign["campaign_id"])
        collaborator_ids = {row["creator_id"] for row in collaborators if row.get("creator_id")}
        comment = await _fetch_comment_payload(
            conn,
            inserted["comment_id"],
            current_user.id,
            campaign["creator_id"],
            friend_ids,
            collaborator_ids,
        )

    return {"comment": comment}


@router.patch("/{campaign_url}/comments/{comment_id}")
async def update_comment(
    campaign_url: str,
    comment_id: int,
    payload: UpdateCommentRequest,
    current_user: User = Depends(get_current_user),
):
    campaign = await _get_campaign_by_url_or_id(campaign_url)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    comment_text = _validate_comment_text(payload.comment_text)
    pool = await get_pool()

    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            """
            SELECT comment_id, creator_id, campaign_id
            FROM comments
            WHERE comment_id = $1
            """,
            comment_id,
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Comment not found")
        if existing["campaign_id"] != campaign["campaign_id"]:
            raise HTTPException(status_code=400, detail="Comment does not belong to this campaign")
        if existing["creator_id"] != current_user.id:
            raise HTTPException(status_code=403, detail="You can only edit your own comments")

        await conn.execute(
            """
            UPDATE comments
            SET comment_text = $2,
                updated_at = NOW()
            WHERE comment_id = $1
            """,
            comment_id,
            comment_text,
        )

        friend_ids = await _get_friend_ids_for_user(conn, current_user.id)
        collaborators = await _get_campaign_collaborators(conn, campaign["campaign_id"])
        collaborator_ids = {row["creator_id"] for row in collaborators if row.get("creator_id")}
        comment = await _fetch_comment_payload(
            conn,
            comment_id,
            current_user.id,
            campaign["creator_id"],
            friend_ids,
            collaborator_ids,
        )

    return {"comment": comment}


@router.post("/{campaign_url}/comments/{comment_id}/like")
async def like_comment(
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
            SELECT comment_id, campaign_id
            FROM comments
            WHERE comment_id = $1
            """,
            comment_id,
        )
        if not comment:
            raise HTTPException(status_code=404, detail="Comment not found")
        if comment["campaign_id"] != campaign["campaign_id"]:
            raise HTTPException(status_code=400, detail="Comment does not belong to this campaign")

        await conn.execute(
            """
            INSERT INTO comment_likes (comment_id, creator_id)
            VALUES ($1, $2)
            ON CONFLICT (comment_id, creator_id) DO NOTHING
            """,
            comment_id,
            current_user.id,
        )

        like_count = await conn.fetchval(
            "SELECT COUNT(*) FROM comment_likes WHERE comment_id = $1",
            comment_id,
        )

    return {"liked": True, "like_count": int(like_count), "comment_id": comment_id}


@router.delete("/{campaign_url}/comments/{comment_id}/like")
async def unlike_comment(
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
            SELECT comment_id, campaign_id
            FROM comments
            WHERE comment_id = $1
            """,
            comment_id,
        )
        if not comment:
            raise HTTPException(status_code=404, detail="Comment not found")
        if comment["campaign_id"] != campaign["campaign_id"]:
            raise HTTPException(status_code=400, detail="Comment does not belong to this campaign")

        await conn.execute(
            """
            DELETE FROM comment_likes
            WHERE comment_id = $1
              AND creator_id = $2
            """,
            comment_id,
            current_user.id,
        )

        like_count = await conn.fetchval(
            "SELECT COUNT(*) FROM comment_likes WHERE comment_id = $1",
            comment_id,
        )

    return {"liked": False, "like_count": int(like_count), "comment_id": comment_id}


@router.post("/{campaign_url}/report")
async def report_campaign(
    campaign_url: str,
    payload: ReportCampaignRequest,
    current_user: User = Depends(get_current_user),
):
    campaign = await _get_campaign_by_url_or_id(campaign_url)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    pool = await get_pool()

    async with pool.acquire() as conn:
        creator = await conn.fetchrow(
            """
            SELECT creator_id, name, last_name, user_type
            FROM creators
            WHERE creator_id = $1
            """,
            campaign["creator_id"],
        )

        inserted = await conn.fetchrow(
            """
            INSERT INTO campaign_reports (
                reporter_creator_id,
                reported_campaign_id,
                reported_campaign_creator_id,
                reported_campaign_creator_name,
                reported_campaign_creator_last_name,
                reported_campaign_creator_username,
                reported_campaign_creator_user_type,
                reported_campaign_title_snapshot,
                reported_campaign_status_snapshot,
                reported_campaign_url_snapshot,
                reported_campaign_description_html_snapshot,
                reported_campaign_category_snapshot,
                reported_campaign_location_snapshot,
                reported_campaign_funding_goal_cents_snapshot,
                reported_campaign_duration_days_snapshot,
                reported_campaign_amount_raised_cents_snapshot,
                reported_campaign_backers_snapshot,
                reported_campaign_time_created_snapshot,
                reported_campaign_end_date_snapshot,
                reported_campaign_bio_snapshot,
                reason,
                notes,
                status
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7,
                $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20,
                NULLIF($21, ''), NULLIF($22, ''), 'open'
            )
            RETURNING report_id, time_reported
            """,
            current_user.id,
            campaign["campaign_id"],
            campaign["creator_id"],
            creator["name"] if creator else None,
            creator["last_name"] if creator else None,
            campaign["creator_id"],
            creator["user_type"] if creator else None,
            campaign.get("title"),
            campaign.get("status"),
            campaign.get("url"),
            campaign.get("description_html"),
            campaign.get("category"),
            campaign.get("location"),
            campaign.get("funding_goal_cents"),
            campaign.get("duration_days"),
            campaign.get("amount_raised_cents"),
            campaign.get("backers"),
            campaign.get("time_created"),
            campaign.get("end_date"),
            campaign.get("bio"),
            (payload.reason or "").strip(),
            (payload.notes or "").strip(),
        )

        photos = await conn.fetch(
            """
            SELECT
                photo_id,
                campaign_id,
                s3_bucket,
                s3_key,
                content_type,
                file_size_bytes,
                width_px,
                height_px,
                is_primary,
                sort_order,
                uploaded_by_creator_id,
                time_created
            FROM campaign_photos
            WHERE campaign_id = $1
            ORDER BY is_primary DESC, sort_order ASC NULLS LAST, photo_id ASC
            """,
            campaign["campaign_id"],
        )

        for photo in photos:
            await conn.execute(
                """
                INSERT INTO campaign_report_photos (
                    report_id,
                    reported_photo_id,
                    reported_campaign_id,
                    s3_bucket_snapshot,
                    s3_key_snapshot,
                    image_url_snapshot,
                    content_type_snapshot,
                    file_size_bytes_snapshot,
                    width_px_snapshot,
                    height_px_snapshot,
                    is_primary_snapshot,
                    sort_order_snapshot,
                    uploaded_by_creator_id_snapshot,
                    photo_time_created_snapshot
                )
                VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14
                )
                """,
                inserted["report_id"],
                photo["photo_id"],
                photo["campaign_id"],
                photo["s3_bucket"],
                photo["s3_key"],
                f"https://{photo['s3_bucket']}.s3.us-east-2.amazonaws.com/{photo['s3_key']}" if photo["s3_bucket"] and photo["s3_key"] else None,
                photo["content_type"],
                photo["file_size_bytes"],
                photo["width_px"],
                photo["height_px"],
                photo["is_primary"],
                photo["sort_order"],
                photo["uploaded_by_creator_id"],
                photo["time_created"],
            )

    return {
        "ok": True,
        "report_id": inserted["report_id"],
        "campaign_id": campaign["campaign_id"],
        "status": "open",
        "time_reported": inserted["time_reported"],
    }


@router.post("/{campaign_url}/comments/{comment_id}/report")
async def report_comment(
    campaign_url: str,
    comment_id: int,
    payload: ReportCommentRequest = ReportCommentRequest(),
    current_user: User = Depends(get_current_user),
):
    campaign = await _get_campaign_by_url_or_id(campaign_url)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    pool = await get_pool()
    async with pool.acquire() as conn:
        comment = await conn.fetchrow(
            """
            SELECT
                c.comment_id,
                c.campaign_id,
                c.creator_id,
                c.comment_text,
                c.time_created,
                c.updated_at,
                cr.name,
                cr.last_name
            FROM comments c
            LEFT JOIN creators cr ON cr.creator_id = c.creator_id
            WHERE c.comment_id = $1
            """,
            comment_id,
        )
        if not comment:
            raise HTTPException(status_code=404, detail="Comment not found")
        if comment["campaign_id"] != campaign["campaign_id"]:
            raise HTTPException(status_code=400, detail="Comment does not belong to this campaign")

        inserted = await conn.fetchrow(
            """
            INSERT INTO comment_reports (
                reporter_creator_id,
                reported_comment_id,
                reported_campaign_id,
                reported_comment_creator_id,
                reported_comment_creator_name,
                reported_comment_creator_last_name,
                reported_comment_creator_username,
                reported_comment_text_snapshot,
                reported_comment_time_created,
                reported_comment_updated_at_snapshot,
                reason,
                notes,
                status
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NULLIF($11, ''), NULLIF($12, ''), 'open'
            )
            RETURNING report_id, time_reported
            """,
            current_user.id,
            comment["comment_id"],
            comment["campaign_id"],
            comment["creator_id"],
            comment["name"],
            comment["last_name"],
            comment["creator_id"],
            comment["comment_text"],
            comment["time_created"],
            comment["updated_at"],
            (payload.reason or "").strip(),
            (payload.notes or "").strip(),
        )

    return {
        "ok": True,
        "report_id": inserted["report_id"],
        "comment_id": comment_id,
        "status": "open",
        "time_reported": inserted["time_reported"],
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
            SELECT comment_id, creator_id, campaign_id, parent_comment_id
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

        if comment["parent_comment_id"] is None:
            await conn.execute(
                "DELETE FROM comment_likes WHERE comment_id IN (SELECT comment_id FROM comments WHERE comment_id = $1 OR parent_comment_id = $1)",
                comment_id,
            )
            await conn.execute(
                "DELETE FROM comments WHERE comment_id = $1 OR parent_comment_id = $1",
                comment_id,
            )
        else:
            await conn.execute("DELETE FROM comment_likes WHERE comment_id = $1", comment_id)
            await conn.execute("DELETE FROM comments WHERE comment_id = $1", comment_id)

    return {"ok": True, "comment_id": comment_id}


@router.patch("/{campaign_url}/edit")
async def edit_campaign(
    campaign_url: str,
    payload: EditCampaignRequest,
    current_user: User = Depends(get_current_user),
):
    campaign = await _get_campaign_by_url_or_id(campaign_url)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    pool = await get_pool()
    async with pool.acquire() as conn:
        viewer_id = current_user.id
        is_owner = campaign["creator_id"] == viewer_id

        viewer = await conn.fetchrow(
            """
            SELECT email
            FROM creators
            WHERE creator_id = $1
            LIMIT 1
            """,
            viewer_id,
        )
        viewer_email = ((viewer["email"] if viewer else None) or "").strip()

        is_collaborator = False
        if viewer_email:
            collaborator_row = await conn.fetchrow(
                """
                SELECT 1
                FROM collaborators
                WHERE campaign_id = $1
                  AND LOWER(email) = LOWER($2)
                  AND LOWER(COALESCE(status, '')) = 'accepted'
                LIMIT 1
                """,
                campaign["campaign_id"],
                viewer_email,
            )
            is_collaborator = collaborator_row is not None

        if not is_owner and not is_collaborator:
            raise HTTPException(status_code=403, detail="You do not have permission to edit this campaign")

        _validate_campaign_edit_payload(payload, campaign)

        if is_collaborator and not is_owner:
            if payload.title.strip() != (campaign.get("title") or "").strip():
                raise HTTPException(status_code=403, detail="Collaborators cannot edit the title")
            if (payload.category or "").strip() != (campaign.get("category") or "").strip():
                raise HTTPException(status_code=403, detail="Collaborators cannot edit the category")
            if (payload.location or "").strip() != (campaign.get("location") or "").strip():
                raise HTTPException(status_code=403, detail="Collaborators cannot edit the location")
            if int(payload.funding_goal_cents) != int(campaign.get("funding_goal_cents") or 0):
                raise HTTPException(status_code=403, detail="Collaborators cannot edit the funding goal")
            if payload.duration_days != campaign.get("duration_days"):
                raise HTTPException(status_code=403, detail="Collaborators cannot edit the duration")

        async with conn.transaction():
            updated = await conn.fetchrow(
                """
                UPDATE campaigns
                SET title = $2,
                    description_html = $3,
                    category = $4,
                    location = $5,
                    funding_goal_cents = $6,
                    duration_days = $7
                WHERE campaign_id = $1
                RETURNING *
                """,
                campaign["campaign_id"],
                payload.title.strip(),
                payload.description_html.strip(),
                payload.category.strip() if payload.category else None,
                payload.location.strip() if payload.location else None,
                payload.funding_goal_cents,
                payload.duration_days,
            )

            await conn.execute("DELETE FROM rewards WHERE campaign_id = $1", campaign["campaign_id"])
            for idx, reward in enumerate(payload.rewards):
                await conn.execute(
                    """
                    INSERT INTO rewards (campaign_id, title, required_amount_cents, description, limit_total, display_order)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    campaign["campaign_id"],
                    reward.title.strip(),
                    reward.required_amount_cents,
                    reward.description.strip() if reward.description else None,
                    reward.limit_total,
                    idx,
                )

            await conn.execute("DELETE FROM faqs WHERE campaign_id = $1", campaign["campaign_id"])
            for idx, faq in enumerate(payload.faqs):
                await conn.execute(
                    """
                    INSERT INTO faqs (campaign_id, display_order, question, answer)
                    VALUES ($1, $2, $3, $4)
                    """,
                    campaign["campaign_id"],
                    idx,
                    faq.question.strip(),
                    faq.answer.strip(),
                )

            await conn.execute("DELETE FROM campaign_photos WHERE campaign_id = $1", campaign["campaign_id"])
            has_primary = any(photo.is_primary for photo in payload.photos)
            for idx, photo in enumerate(payload.photos):
                await conn.execute(
                    """
                    INSERT INTO campaign_photos (
                        campaign_id,
                        s3_bucket,
                        s3_key,
                        content_type,
                        is_primary,
                        sort_order,
                        uploaded_by_creator_id
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                    campaign["campaign_id"],
                    photo.s3_bucket.strip(),
                    photo.s3_key.strip(),
                    photo.content_type.strip() if photo.content_type else "image/jpeg",
                    photo.is_primary if has_primary else idx == 0,
                    idx,
                    current_user.id,
                )

    return {"ok": True, "campaign": dict(updated)}


@router.delete("/{campaign_url}")
async def delete_campaign(
    campaign_url: str,
    current_user: User = Depends(get_current_user),
):
    campaign = await _get_campaign_by_url_or_id(campaign_url)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign["creator_id"] != current_user.id:
        raise HTTPException(status_code=403, detail="You can only delete your own campaign")
    if campaign["status"] != "draft":
        raise HTTPException(status_code=400, detail="Only draft campaigns can be deleted")

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM campaign_photos WHERE campaign_id = $1", campaign["campaign_id"])
            await conn.execute("DELETE FROM rewards WHERE campaign_id = $1", campaign["campaign_id"])
            await conn.execute("DELETE FROM faqs WHERE campaign_id = $1", campaign["campaign_id"])
            await conn.execute("DELETE FROM comments WHERE campaign_id = $1", campaign["campaign_id"])
            await conn.execute("DELETE FROM campaigns WHERE campaign_id = $1", campaign["campaign_id"])

    return {"ok": True, "campaign_id": campaign["campaign_id"]}