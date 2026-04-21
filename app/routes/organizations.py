"""
Organizations routes — membership queries and business payment details
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

import db as db_mod
from app.auth import get_current_user
from app.database import get_db
from app.models.models import User, PaymentDetail, AccountType
from app.models.schemas import PaymentDetailCreate, PaymentDetailResponse

router = APIRouter(prefix="/api/organizations", tags=["organizations"])


@router.get("/my-memberships")
async def get_my_memberships(user: User = Depends(get_current_user)):
    """Return all businesses the authenticated user is a member of."""
    pool = await db_mod.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT om.organization_id,
                   om.role,
                   c.name,
                   c.bio,
                   c.avatar_url
            FROM organization_members om
            JOIN creators c ON c.creator_id = om.organization_id
            WHERE om.member_id = $1
              AND c.user_type = 0
            ORDER BY c.name ASC
            """,
            user.id,
        )
    return [
        {
            "organization_id": r["organization_id"],
            "name": r["name"] or "",
            "bio": r["bio"] or "",
            "logo_url": r["avatar_url"],
            "role": r["role"],
        }
        for r in rows
    ]


@router.get("/{org_id}/my-role")
async def get_my_role(org_id: str, user: User = Depends(get_current_user)):
    """Return this business's details + the authenticated user's role, or 403."""
    pool = await db_mod.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT om.role,
                   c.name,
                   c.bio,
                   c.avatar_url
            FROM organization_members om
            JOIN creators c ON c.creator_id = om.organization_id
            WHERE om.member_id = $1 AND om.organization_id = $2
            """,
            user.id,
            org_id,
        )
    if not row:
        raise HTTPException(status_code=403, detail="Not a member of this organization")
    return {
        "organization_id": org_id,
        "name": row["name"] or "",
        "bio": row["bio"] or "",
        "logo_url": row["avatar_url"],
        "role": row["role"],
    }


# Roles that can access finance data
FINANCE_ROLES = {"finance", "admin", "owner"}
# Roles that can create/edit campaigns
CAMPAIGN_EDIT_ROLES = {"campaign_editor", "admin", "owner"}


async def _require_role(org_id: str, user_id: str, allowed: set) -> str:
    """Return the user's role if it is in allowed, else raise 403."""
    pool = await db_mod.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT role FROM organization_members WHERE member_id = $1 AND organization_id = $2",
            user_id, org_id,
        )
    if not row:
        raise HTTPException(status_code=403, detail="Not a member of this organization")
    role = row["role"]
    if role not in allowed:
        raise HTTPException(status_code=403, detail=f"Role '{role}' cannot perform this action")
    return role


@router.get("/{org_id}/campaigns")
async def get_org_campaigns(org_id: str, user: User = Depends(get_current_user)):
    """Return campaigns created by this organization. Requires membership."""
    pool = await db_mod.get_pool()
    async with pool.acquire() as conn:
        member = await conn.fetchrow(
            "SELECT role FROM organization_members WHERE member_id = $1 AND organization_id = $2",
            user.id, org_id,
        )
        if not member:
            raise HTTPException(status_code=403, detail="Not a member of this organization")

        rows = await conn.fetch(
            """
            SELECT c.campaign_id,
                   c.title,
                   c.url AS slug,
                   c.status,
                   c.category,
                   c.time_created,
                   cp.s3_bucket AS photo_bucket,
                   cp.s3_key   AS photo_key,
                   COALESCE(c.amount_raised_cents, 0) / 100.0 AS raised_amount,
                   COALESCE(c.funding_goal_cents, 0) / 100.0 AS goal_amount,
                   CASE
                     WHEN COALESCE(c.funding_goal_cents, 0) > 0
                     THEN ROUND((COALESCE(c.amount_raised_cents, 0)::numeric / c.funding_goal_cents::numeric) * 100, 1)
                     ELSE 0
                   END AS funding_percentage,
                   COALESCE(c.backers, 0) AS donors_count
            FROM campaigns c
            LEFT JOIN LATERAL (
                SELECT s3_bucket, s3_key
                FROM campaign_photos
                WHERE campaign_id = c.campaign_id
                ORDER BY is_primary DESC, sort_order ASC NULLS LAST, photo_id ASC
                LIMIT 1
            ) cp ON TRUE
            WHERE c.creator_id = $1
            ORDER BY c.time_created DESC
            """,
            org_id,
        )

    results = []
    for r in rows:
        row = dict(r)
        bucket, key = row.pop("photo_bucket", None), row.pop("photo_key", None)
        row["image_url"] = (
            f"https://{bucket}.s3.us-east-2.amazonaws.com/{key}"
            if bucket and key else None
        )
        tc = row.get("time_created")
        row["created_at"] = tc.isoformat() if tc else None
        results.append(row)
    return results


INVITABLE_ROLES = {"admin", "finance", "campaign_editor", "viewer"}


class InviteMemberRequest(BaseModel):
    email: str
    role: str


@router.get("/{org_id}/members")
async def list_org_members(
    org_id: str,
    user: User = Depends(get_current_user),
):
    """List all members of the organization. Owner only."""
    await _require_role(org_id, user.id, {"owner"})
    pool = await db_mod.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT om.member_id,
                   om.role,
                   c.name,
                   c.last_name,
                   c.username,
                   c.avatar_url,
                   c.email
            FROM organization_members om
            JOIN creators c ON c.creator_id = om.member_id
            WHERE om.organization_id = $1
            ORDER BY
              CASE om.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END,
              om.added_at ASC NULLS LAST
            """,
            org_id,
        )
    return [
        {
            "member_id": r["member_id"],
            "name": r["name"] or "",
            "last_name": r["last_name"] or "",
            "username": r["username"] or r["member_id"],
            "avatar_url": r["avatar_url"],
            "role": r["role"],
            "email": r["email"] or "",
        }
        for r in rows
    ]


@router.post("/{org_id}/members")
async def invite_org_member(
    org_id: str,
    data: InviteMemberRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Invite a user by email and assign them a role. Owner only."""
    await _require_role(org_id, user.id, {"owner"})

    if data.role not in INVITABLE_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role '{data.role}'")

    # Find the target user by email — must be an individual account (user_type=1)
    result = await db.execute(select(User).where(User.email == data.email.strip().lower()))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="No account found with that email address")
    if target.user_type != 1:
        raise HTTPException(status_code=400, detail="Only individual accounts can be invited as members")

    pool = await db_mod.get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT role FROM organization_members WHERE member_id = $1 AND organization_id = $2",
            target.id, org_id,
        )
        if existing:
            raise HTTPException(status_code=409, detail="This user is already a member")

        await conn.execute(
            "INSERT INTO organization_members (member_id, organization_id, role, added_by) "
            "VALUES ($1, $2, $3, $4)",
            target.id, org_id, data.role, user.id,
        )

    return {
        "member_id": target.id,
        "name": target.name or "",
        "last_name": getattr(target, "last_name", None) or "",
        "username": target.username or target.id,
        "avatar_url": getattr(target, "avatar_url", None),
        "role": data.role,
        "email": target.email or "",
    }


class UpdateRoleRequest(BaseModel):
    role: str


@router.put("/{org_id}/members/{member_id}")
async def update_member_role(
    org_id: str,
    member_id: str,
    data: UpdateRoleRequest,
    user: User = Depends(get_current_user),
):
    """Change a member's role. Owner only. Cannot change the owner role."""
    await _require_role(org_id, user.id, {"owner"})

    if data.role not in INVITABLE_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role '{data.role}'")

    pool = await db_mod.get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT role FROM organization_members WHERE member_id = $1 AND organization_id = $2",
            member_id, org_id,
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Member not found in this organization")
        if existing["role"] == "owner":
            raise HTTPException(status_code=403, detail="Cannot change the owner's role")

        await conn.execute(
            "UPDATE organization_members SET role = $1 WHERE member_id = $2 AND organization_id = $3",
            data.role, member_id, org_id,
        )
    return {"member_id": member_id, "role": data.role}


@router.post("/{org_id}/payment-details", response_model=PaymentDetailResponse)
async def save_org_payment_details(
    org_id: str,
    data: PaymentDetailCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_role(org_id, user.id, FINANCE_ROLES)
    detail = PaymentDetail(
        user_id=org_id,
        account_type=AccountType(data.account_type),
        account_holder_name=data.account_holder_name,
        routing_number_last4=data.routing_number[-4:],
        account_number_last4=data.account_number[-4:],
        is_verified=False,
        is_default=True,
    )
    db.add(detail)
    await db.flush()
    return PaymentDetailResponse(
        id=detail.id, user_id=detail.user_id,
        account_type=detail.account_type.value,
        account_holder_name=detail.account_holder_name,
        routing_number_last4=detail.routing_number_last4,
        account_number_last4=detail.account_number_last4,
        is_verified=detail.is_verified, is_default=detail.is_default,
        created_at=detail.created_at,
    )


@router.get("/{org_id}/payment-details")
async def get_org_payment_details(
    org_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_role(org_id, user.id, FINANCE_ROLES)
    result = await db.execute(select(PaymentDetail).where(PaymentDetail.user_id == org_id))
    return [
        PaymentDetailResponse(
            id=d.id, user_id=d.user_id,
            account_type=d.account_type.value if isinstance(d.account_type, AccountType) else d.account_type,
            account_holder_name=d.account_holder_name,
            routing_number_last4=d.routing_number_last4,
            account_number_last4=d.account_number_last4,
            is_verified=d.is_verified, is_default=d.is_default,
            created_at=d.created_at,
        )
        for d in result.scalars().all()
    ]


# ── Org Campaign Drafts ───────────────────────────────────────────────────────

@router.put("/{org_id}/campaigns/drafts")
async def save_org_draft(
    org_id: str,
    data: dict,
    user: User = Depends(get_current_user),
):
    """Create or update a draft campaign owned by the org. Creator_id = org_id."""
    await _require_role(org_id, user.id, CAMPAIGN_EDIT_ROLES)
    data["creator_id"] = org_id
    try:
        return await db_mod.upsert_draft_campaign(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{org_id}/campaigns/drafts/{campaign_id}")
async def get_org_draft(
    org_id: str,
    campaign_id: int,
    user: User = Depends(get_current_user),
):
    """Get a single org draft campaign."""
    await _require_role(org_id, user.id, CAMPAIGN_EDIT_ROLES)
    result = await db_mod.get_draft_campaign(campaign_id, org_id)
    if not result:
        raise HTTPException(status_code=404, detail="Draft not found")
    return result


@router.put("/{org_id}/campaigns/drafts/{campaign_id}/photos")
async def replace_org_draft_photos(
    org_id: str,
    campaign_id: int,
    data: dict,
    user: User = Depends(get_current_user),
):
    """Replace all photos for an org draft campaign."""
    await _require_role(org_id, user.id, CAMPAIGN_EDIT_ROLES)
    try:
        await db_mod.replace_campaign_photos(campaign_id, org_id, data.get("photos", []))
        return {"ok": True, "campaign_id": campaign_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{org_id}/campaigns/drafts/{campaign_id}")
async def delete_org_draft(
    org_id: str,
    campaign_id: int,
    user: User = Depends(get_current_user),
):
    """Hard-delete a draft campaign owned by the org."""
    await _require_role(org_id, user.id, CAMPAIGN_EDIT_ROLES)
    pool = await db_mod.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, creator_id FROM campaigns WHERE campaign_id = $1",
            campaign_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Draft not found")
        if row["creator_id"] != org_id:
            raise HTTPException(status_code=403, detail="This draft does not belong to this organization")
        if row["status"] != "draft":
            raise HTTPException(status_code=400, detail="Only drafts can be deleted")
        await conn.execute("DELETE FROM faqs WHERE campaign_id = $1", campaign_id)
        await conn.execute("DELETE FROM rewards WHERE campaign_id = $1", campaign_id)
        await conn.execute("DELETE FROM collaborators WHERE campaign_id = $1", campaign_id)
        await conn.execute("DELETE FROM campaign_photos WHERE campaign_id = $1", campaign_id)
        await conn.execute("DELETE FROM campaigns WHERE campaign_id = $1", campaign_id)
    return {"status": "deleted", "campaign_id": campaign_id}


@router.post("/{org_id}/campaigns/finalize")
async def finalize_org_campaign(
    org_id: str,
    data: dict,
    user: User = Depends(get_current_user),
):
    """Finalize an org campaign. Forces creator_id = org_id."""
    await _require_role(org_id, user.id, CAMPAIGN_EDIT_ROLES)
    data["creator_id"] = org_id
    try:
        return await db_mod.finalize_campaign(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
