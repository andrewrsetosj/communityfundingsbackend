from __future__ import annotations

import os
from typing import Optional, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from cryptography.fernet import Fernet, InvalidToken


# ─────────────────────────────────────────────────────────────
# Encryption helpers (BANK_ENCRYPTION_KEY in .env)
# ─────────────────────────────────────────────────────────────

def _get_fernet() -> Fernet:
    key = os.getenv("BANK_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("BANK_ENCRYPTION_KEY is not set")
    return Fernet(key.encode("ascii"))

def encrypt_str(plaintext: Optional[str]) -> Optional[str]:
    if plaintext is None:
        return None
    token = _get_fernet().encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")

def decrypt_str(token: Optional[str]) -> Optional[str]:
    if token is None:
        return None
    try:
        plaintext = _get_fernet().decrypt(token.encode("ascii"))
    except InvalidToken as e:
        raise ValueError("Invalid token or wrong BANK_ENCRYPTION_KEY") from e
    return plaintext.decode("utf-8")


# ─────────────────────────────────────────────────────────────
# Finalize campaign (campaign + rewards + faqs + bank details)
# ─────────────────────────────────────────────────────────────

async def finalize_campaign_submission(
    *,
    db: AsyncSession,
    creator_id: str,                    # MUST exist in public.creators(creator_id)
    title: str | None,
    url: str | None = None,             # maps to campaigns.url (unique)
    description: str | None = None,
    category: str | None = None,
    location: str | None = None,        # maps to campaigns."location"
    funding_goal_cents: int | None = None,
    duration_days: int | None = None,
    end_date=None,                      # datetime | None (timestamptz)
    status: str = "draft",              # "draft" or "review"

    rewards: list[dict[str, Any]] | None = None,
    faqs: list[dict[str, Any]] | None = None,

    # Bank details (encrypt before insert)
    bank: dict[str, Any] | None = None, # {account_type, account_holder_name, routing_number, account_number}
) -> int:
    """
    Writes to:
      - public.campaigns
      - public.campaign_rewards
      - public.campaign_faqs
      - public.campaign_bank_details (encrypted routing/account)

    Returns campaign_id (bigint).
    """

    if status not in {"draft", "review"}:
        raise ValueError("status must be 'draft' or 'review'")

    rewards = rewards or []
    faqs = faqs or []

    # Basic validation to avoid constraint errors
    for r in rewards:
        if int(r["required_amount_cents"]) <= 0:
            raise ValueError("reward.required_amount_cents must be > 0")
        if int(r.get("display_order", 0)) < 0:
            raise ValueError("reward.display_order must be >= 0")

    for f in faqs:
        if int(f.get("display_order", 0)) < 0:
            raise ValueError("faq.display_order must be >= 0")

    if bank is not None:
        acct_type = bank.get("account_type")
        if acct_type not in {"individual", "business"}:
            raise ValueError("bank.account_type must be 'individual' or 'business'")
        if not bank.get("account_holder_name"):
            raise ValueError("bank.account_holder_name is required")
        if not bank.get("routing_number"):
            raise ValueError("bank.routing_number is required")
        if not bank.get("account_number"):
            raise ValueError("bank.account_number is required")

    async with db.begin():
        # 1) Insert campaign
        res = await db.execute(
            text("""
                INSERT INTO public.campaigns
                    (creator_id, title, status, time_created, url, description, category, "location",
                     funding_goal_cents, duration_days, end_date)
                VALUES
                    (:creator_id, :title, :status, NOW(), :url, :description, :category, :location,
                     :funding_goal_cents, :duration_days, :end_date)
                RETURNING campaign_id
            """),
            {
                "creator_id": creator_id,
                "title": title,
                "status": status,
                "url": url,
                "description": description,
                "category": category,
                "location": location,
                "funding_goal_cents": funding_goal_cents,
                "duration_days": duration_days,
                "end_date": end_date,
            }
        )
        campaign_id = res.scalar_one()

        # 2) Insert rewards
        if rewards:
            await db.execute(
                text("""
                    INSERT INTO public.campaign_rewards
                        (campaign_id, title, description, required_amount_cents, display_order)
                    VALUES
                        (:campaign_id, :title, :description, :required_amount_cents, :display_order)
                """),
                [
                    {
                        "campaign_id": campaign_id,
                        "title": r["title"],
                        "description": r.get("description"),
                        "required_amount_cents": int(r["required_amount_cents"]),
                        "display_order": int(r.get("display_order", 0)),
                    }
                    for r in rewards
                ],
            )

        # 3) Insert faqs
        if faqs:
            await db.execute(
                text("""
                    INSERT INTO public.campaign_faqs
                        (campaign_id, question, answer, display_order)
                    VALUES
                        (:campaign_id, :question, :answer, :display_order)
                """),
                [
                    {
                        "campaign_id": campaign_id,
                        "question": f["question"],
                        "answer": f["answer"],
                        "display_order": int(f.get("display_order", 0)),
                    }
                    for f in faqs
                ],
            )

        # 4) Insert bank details (encrypted)
        if bank is not None:
            routing_enc = encrypt_str(str(bank["routing_number"]))
            account_enc = encrypt_str(str(bank["account_number"]))

            await db.execute(
                text("""
                    INSERT INTO public.campaign_bank_details
                        (campaign_id, account_type, account_holder_name, routing_number, account_number)
                    VALUES
                        (:campaign_id, :account_type, :account_holder_name, :routing_number, :account_number)
                """),
                {
                    "campaign_id": campaign_id,
                    "account_type": bank["account_type"],
                    "account_holder_name": bank["account_holder_name"],
                    "routing_number": routing_enc,
                    "account_number": account_enc,
                },
            )

    return int(campaign_id)