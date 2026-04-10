from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import get_current_user
from app.db import get_pool
from app.models.models import User

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


async def _create_missing_notifications_for_user(conn, recipient_creator_id: str) -> int:
    inserted_count = 0

    follower_rows = await conn.fetch(
        """
        SELECT
            cf.follower_creator_id AS actor_creator_id,
            cf.followed_creator_id AS recipient_creator_id,
            cf.time_created,
            'follow:' || cf.follower_creator_id || ':' || cf.followed_creator_id AS source_key,
            follower.name,
            follower.last_name,
            COALESCE(NULLIF(follower.username, ''), follower.creator_id) AS follower_username
        FROM creator_follows cf
        JOIN creators follower
          ON follower.creator_id = cf.follower_creator_id
        WHERE cf.followed_creator_id = $1
          AND cf.follower_creator_id <> $1
        ORDER BY cf.time_created DESC
        """,
        recipient_creator_id,
    )

    for row in follower_rows:
        display_name = " ".join(
            part for part in [row["name"], row["last_name"]] if part
        ).strip() or row["follower_username"] or "Someone"

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
                is_read,
                is_deleted,
                time_created
            )
            VALUES (
                $1, $2, 'new_follower', 'follow', $3,
                $4, $5, $6, FALSE, FALSE, $7
            )
            ON CONFLICT (recipient_creator_id, source_type, source_key) DO NOTHING
            RETURNING notification_id
            """,
            row["recipient_creator_id"],
            row["actor_creator_id"],
            row["source_key"],
            f"{display_name} followed you",
            "View their profile.",
            f"/profile/{row['follower_username'] or row['actor_creator_id']}",
            row["time_created"],
        )
        if inserted:
            inserted_count += 1

    campaign_comment_rows = await conn.fetch(
        """
        SELECT
            c.comment_id,
            c.creator_id AS actor_creator_id,
            camp.creator_id AS recipient_creator_id,
            camp.campaign_id,
            camp.url AS campaign_url,
            camp.title AS campaign_title,
            c.comment_text,
            c.time_created,
            commenter.name,
            commenter.last_name,
            COALESCE(NULLIF(commenter.username, ''), commenter.creator_id) AS commenter_username
        FROM comments c
        JOIN campaigns camp
          ON camp.campaign_id = c.campaign_id
        JOIN creators commenter
          ON commenter.creator_id = c.creator_id
        WHERE camp.creator_id = $1
          AND c.creator_id <> $1
        ORDER BY c.time_created DESC
        """,
        recipient_creator_id,
    )

    for row in campaign_comment_rows:
        display_name = " ".join(
            part for part in [row["name"], row["last_name"]] if part
        ).strip() or row["commenter_username"] or "Someone"

        preview = (row["comment_text"] or "").strip()
        if len(preview) > 120:
            preview = preview[:117] + "..."

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
                comment_id,
                is_read,
                is_deleted,
                time_created
            )
            VALUES (
                $1, $2, 'comment_on_campaign', 'comment_on_campaign', $3,
                $4, $5, $6, $7, $8, FALSE, FALSE, $9
            )
            ON CONFLICT (recipient_creator_id, source_type, source_key) DO NOTHING
            RETURNING notification_id
            """,
            row["recipient_creator_id"],
            row["actor_creator_id"],
            f"comment:{row['comment_id']}",
            f"{display_name} commented on {row['campaign_title']}",
            preview or "Open campaign comment thread.",
            f"/project/{row['campaign_url'] or row['campaign_id']}",
            row["campaign_id"],
            row["comment_id"],
            row["time_created"],
        )
        if inserted:
            inserted_count += 1

    reply_rows = await conn.fetch(
        """
        SELECT
            reply.comment_id,
            reply.creator_id AS actor_creator_id,
            parent.creator_id AS recipient_creator_id,
            reply.campaign_id,
            camp.url AS campaign_url,
            camp.title AS campaign_title,
            reply.comment_text,
            reply.time_created,
            replier.name,
            replier.last_name,
            COALESCE(NULLIF(replier.username, ''), replier.creator_id) AS replier_username
        FROM comments reply
        JOIN comments parent
          ON parent.comment_id = reply.reply_to_comment_id
        JOIN campaigns camp
          ON camp.campaign_id = reply.campaign_id
        JOIN creators replier
          ON replier.creator_id = reply.creator_id
        WHERE parent.creator_id = $1
          AND reply.creator_id <> $1
        ORDER BY reply.time_created DESC
        """,
        recipient_creator_id,
    )

    for row in reply_rows:
        display_name = " ".join(
            part for part in [row["name"], row["last_name"]] if part
        ).strip() or row["replier_username"] or "Someone"

        preview = (row["comment_text"] or "").strip()
        if len(preview) > 120:
            preview = preview[:117] + "..."

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
                comment_id,
                is_read,
                is_deleted,
                time_created
            )
            VALUES (
                $1, $2, 'reply_to_comment', 'reply_to_comment', $3,
                $4, $5, $6, $7, $8, FALSE, FALSE, $9
            )
            ON CONFLICT (recipient_creator_id, source_type, source_key) DO NOTHING
            RETURNING notification_id
            """,
            row["recipient_creator_id"],
            row["actor_creator_id"],
            f"reply:{row['comment_id']}",
            f"{display_name} replied to your comment",
            preview or f"On {row['campaign_title']}.",
            f"/project/{row['campaign_url'] or row['campaign_id']}",
            row["campaign_id"],
            row["comment_id"],
            row["time_created"],
        )
        if inserted:
            inserted_count += 1

    invite_rows = await conn.fetch(
        """
        SELECT
            coll.collaborator_id,
            invitee.creator_id AS recipient_creator_id,
            inviter.creator_id AS actor_creator_id,
            camp.campaign_id,
            camp.url AS campaign_url,
            camp.title AS campaign_title,
            coll.time_created,
            inviter.name,
            inviter.last_name,
            COALESCE(NULLIF(inviter.username, ''), inviter.creator_id) AS inviter_username
        FROM collaborators coll
        JOIN creators invitee
          ON LOWER(invitee.email) = LOWER(coll.email)
        JOIN campaigns camp
          ON camp.campaign_id = coll.campaign_id
        JOIN creators inviter
          ON inviter.creator_id = camp.creator_id
        WHERE invitee.creator_id = $1
          AND LOWER(COALESCE(coll.status, 'pending')) = 'pending'
          AND inviter.creator_id <> $1
        ORDER BY coll.time_created DESC
        """,
        recipient_creator_id,
    )

    for row in invite_rows:
        display_name = " ".join(
            part for part in [row["name"], row["last_name"]] if part
        ).strip() or row["inviter_username"] or "Someone"

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
                collaborator_id,
                is_read,
                is_deleted,
                time_created
            )
            VALUES (
                $1, $2, 'collaboration_invite', 'collaboration_invite', $3,
                $4, $5, $6, $7, $8, FALSE, FALSE, $9
            )
            ON CONFLICT (recipient_creator_id, source_type, source_key) DO NOTHING
            RETURNING notification_id
            """,
            row["recipient_creator_id"],
            row["actor_creator_id"],
            f"invite:{row['collaborator_id']}",
            f"{display_name} invited you to collaborate",
            f"Campaign: {row['campaign_title']}",
            "/my-projects",
            row["campaign_id"],
            row["collaborator_id"],
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
