from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import get_current_user
from app.db import get_pool
from app.models.models import User

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


async def _create_missing_notifications_for_user(conn, recipient_creator_id: str) -> int:
    inserted_count = 0

    # -----------------------
    # FOLLOWERS
    # -----------------------
    follower_rows = await conn.fetch(...)
    # (unchanged)

    # -----------------------
    # COMMENTS ON CAMPAIGN
    # -----------------------
    campaign_comment_rows = await conn.fetch(...)
    # (unchanged)

    # -----------------------
    # REPLIES
    # -----------------------
    reply_rows = await conn.fetch(...)
    # (unchanged)

    # -----------------------
    # COLLAB INVITES
    # -----------------------
    invite_rows = await conn.fetch(...)
    # (unchanged)

    # =======================
    # 💰 DONATIONS (NEW)
    # =======================
    donation_rows = await conn.fetch(
        """
        SELECT
            d.donation_id,
            d.donor_creator_id AS actor_creator_id,
            camp.creator_id AS recipient_creator_id,
            camp.campaign_id,
            camp.url AS campaign_url,
            camp.title AS campaign_title,
            d.amount,
            d.status,
            d.time_created,
            donor.name,
            donor.last_name,
            COALESCE(NULLIF(donor.username, ''), donor.creator_id) AS donor_username
        FROM donations d
        JOIN campaigns camp
          ON camp.campaign_id = d.campaign_id
        LEFT JOIN creators donor
          ON donor.creator_id = d.donor_creator_id
        WHERE camp.creator_id = $1
          AND d.donor_creator_id <> $1
          AND LOWER(COALESCE(d.status, '')) IN ('succeeded', 'success', 'paid', 'completed')
        ORDER BY d.time_created DESC
        """,
        recipient_creator_id,
    )

    for row in donation_rows:
        display_name = " ".join(
            part for part in [row["name"], row["last_name"]] if part
        ).strip() or row["donor_username"] or "Someone"

        inserted = await conn.fetchval(
            """
            INSERT INTO notifications (
                recipient_creator_id,
                actor_creator_id,
                type,
                source_type,
                source_key,
                title,
                body,
                link_url,
                campaign_id,
                is_read,
                is_deleted,
                time_created
            )
            VALUES (
                $1, $2, 'donation', 'donation', $3,
                $4, $5, $6, $7, FALSE, FALSE, $8
            )
            ON CONFLICT (recipient_creator_id, source_type, source_key) DO NOTHING
            RETURNING notification_id
            """,
            row["recipient_creator_id"],
            row["actor_creator_id"],
            f"donation:{row['donation_id']}",
            f"{display_name} backed {row['campaign_title']}!",
            f"${row['amount']} contribution",
            f"/project/{row['campaign_url'] or row['campaign_id']}",
            row["campaign_id"],
            row["time_created"],
        )

        if inserted:
            inserted_count += 1

    return inserted_count


@router.post("/refresh")
async def refresh_notifications(
    current_user: User = Depends(get_current_user),
):
    pool = await get_pool()

    async with pool.acquire() as conn:
        inserted_count = await _create_missing_notifications_for_user(conn, current_user.id)

        unread_count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM notifications
            WHERE recipient_creator_id = $1
              AND is_deleted = FALSE
              AND is_read = FALSE
            """,
            current_user.id,
        )

    return {
        "ok": True,
        "inserted_count": int(inserted_count),
        "unread_count": int(unread_count or 0),
    }


@router.get("")
async def list_notifications(
    limit: int = Query(20, ge=1, le=100),
    refresh_first: bool = Query(False),
    current_user: User = Depends(get_current_user),
):
    pool = await get_pool()

    async with pool.acquire() as conn:
        if refresh_first:
            await _create_missing_notifications_for_user(conn, current_user.id)

        rows = await conn.fetch(
            """
            SELECT
                n.notification_id,
                n.recipient_creator_id,
                n.actor_creator_id,
                n.type,
                n.title,
                n.body,
                n.link_url,
                n.campaign_id,
                n.comment_id,
                n.collaborator_id,
                n.is_read,
                n.is_deleted,
                n.time_created,
                actor.name AS actor_name,
                actor.last_name AS actor_last_name,
                COALESCE(NULLIF(actor.username, ''), actor.creator_id) AS actor_username,
                actor.avatar_url AS actor_avatar_url
            FROM notifications n
            LEFT JOIN creators actor
              ON actor.creator_id = n.actor_creator_id
            WHERE n.recipient_creator_id = $1
              AND n.is_deleted = FALSE
            ORDER BY n.time_created DESC, n.notification_id DESC
            LIMIT $2
            """,
            current_user.id,
            limit,
        )

        unread_count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM notifications
            WHERE recipient_creator_id = $1
              AND is_deleted = FALSE
              AND is_read = FALSE
            """,
            current_user.id,
        )

    notifications = []
    for row in rows:
        notifications.append(
            {
                "notification_id": row["notification_id"],
                "recipient_creator_id": row["recipient_creator_id"],
                "actor_creator_id": row["actor_creator_id"],
                "type": row["type"],
                "title": row["title"],
                "body": row["body"],
                "link_url": row["link_url"],
                "campaign_id": row["campaign_id"],
                "comment_id": row["comment_id"],
                "collaborator_id": row["collaborator_id"],
                "is_read": bool(row["is_read"]),
                "time_created": row["time_created"],
                "actor": {
                    "creator_id": row["actor_creator_id"],
                    "name": row["actor_name"],
                    "last_name": row["actor_last_name"],
                    "username": row["actor_username"],
                    "avatar_url": row["actor_avatar_url"],
                } if row["actor_creator_id"] else None,
            }
        )

    return {
        "notifications": notifications,
        "unread_count": int(unread_count or 0),
    }


@router.get("/unread-count")
async def get_unread_notification_count(
    refresh_first: bool = Query(False),
    current_user: User = Depends(get_current_user),
):
    pool = await get_pool()

    async with pool.acquire() as conn:
        if refresh_first:
            await _create_missing_notifications_for_user(conn, current_user.id)

        unread_count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM notifications
            WHERE recipient_creator_id = $1
              AND is_deleted = FALSE
              AND is_read = FALSE
            """,
            current_user.id,
        )

    return {"unread_count": int(unread_count or 0)}


@router.post("/{notification_id}/read")
async def mark_notification_read(
    notification_id: int,
    current_user: User = Depends(get_current_user),
):
    pool = await get_pool()

    async with pool.acquire() as conn:
        updated = await conn.fetchrow(
            """
            UPDATE notifications
            SET is_read = TRUE
            WHERE notification_id = $1
              AND recipient_creator_id = $2
              AND is_deleted = FALSE
            RETURNING notification_id
            """,
            notification_id,
            current_user.id,
        )

    if not updated:
        raise HTTPException(status_code=404, detail="Notification not found")

    return {"ok": True, "notification_id": notification_id}


@router.post("/read-all")
async def mark_all_notifications_read(
    current_user: User = Depends(get_current_user),
):
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE notifications
            SET is_read = TRUE
            WHERE recipient_creator_id = $1
              AND is_deleted = FALSE
              AND is_read = FALSE
            """,
            current_user.id,
        )

    return {"ok": True}


@router.delete("/{notification_id}")
async def delete_notification(
    notification_id: int,
    current_user: User = Depends(get_current_user),
):
    pool = await get_pool()

    async with pool.acquire() as conn:
        updated = await conn.fetchrow(
            """
            UPDATE notifications
            SET is_deleted = TRUE
            WHERE notification_id = $1
              AND recipient_creator_id = $2
              AND is_deleted = FALSE
            RETURNING notification_id
            """,
            notification_id,
            current_user.id,
        )

    if not updated:
        raise HTTPException(status_code=404, detail="Notification not found")

    return {"ok": True, "notification_id": notification_id}
