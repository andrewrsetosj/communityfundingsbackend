"""
Business Analytics v2 — per-user analytics for business accounts.

GET /api/business-analytics-v2/{creator_id}

Returns aggregated analytics for a single business creator's campaigns.
Schema-aware to cf-db:
  - donations.amount (numeric, NOT amount_cents)
  - donations.donor_creator_id, donor_name, donor_email
  - donations.platform_fee, net_amount
  - campaigns.amount_raised_cents, funding_goal_cents (BIGINT cents)
  - creators.user_type (smallint, 0=personal, 1=business)
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db

router = APIRouter(prefix="/api/business-analytics-v2", tags=["business-analytics-v2"])


@router.get("/{creator_id}")
async def get_business_analytics(creator_id: str, db: AsyncSession = Depends(get_db)):
    """
    Returns the data needed to render the Business Analytics dashboard
    for one business user. Pure read-only query.
    """

    # 1. Verify creator exists + check user_type
    cr = await db.execute(text("""
        SELECT creator_id, name, last_name, user_type, email
          FROM creators
         WHERE creator_id = :cid
    """), {"cid": creator_id})
    creator = cr.mappings().first()
    if not creator:
        raise HTTPException(status_code=404, detail="Creator not found")

    # 2. Stats grid (5 cards: campaigns, raised, backers, avg, net)
    stats_r = await db.execute(text("""
        SELECT
            count(*) AS total_campaigns,
            count(*) FILTER (WHERE status = 'active') AS active_campaigns,
            COALESCE(sum(amount_raised_cents), 0) / 100.0 AS total_raised,
            COALESCE(sum(backers), 0) AS total_backers,
            CASE WHEN COALESCE(sum(backers), 0) > 0
                 THEN sum(amount_raised_cents) / 100.0 / sum(backers)
                 ELSE 0
            END AS avg_donation
          FROM campaigns
         WHERE creator_id = :cid
    """), {"cid": creator_id})
    stats_row = stats_r.mappings().first() or {}
    stats = dict(stats_row)

    # Net earnings: sum of donations.net_amount for all my campaigns
    net_r = await db.execute(text("""
        SELECT COALESCE(SUM(d.net_amount), 0) AS net_earnings
          FROM donations d
          JOIN campaigns c ON c.campaign_id = d.campaign_id
         WHERE c.creator_id = :cid
           AND d.status = 'succeeded'
    """), {"cid": creator_id})
    net_row = net_r.mappings().first() or {}
    stats["net_earnings"] = float(net_row.get("net_earnings") or 0)
    stats["total_raised"] = float(stats.get("total_raised") or 0)
    stats["avg_donation"] = float(stats.get("avg_donation") or 0)
    stats["total_campaigns"] = int(stats.get("total_campaigns") or 0)
    stats["active_campaigns"] = int(stats.get("active_campaigns") or 0)
    stats["total_backers"] = int(stats.get("total_backers") or 0)

    # 3. Per-campaign performance (for chart + table)
    camps_r = await db.execute(text("""
        SELECT campaign_id,
               title,
               status,
               category,
               location,
               COALESCE(amount_raised_cents, 0) / 100.0 AS raised,
               COALESCE(funding_goal_cents, 0) / 100.0 AS goal,
               COALESCE(backers, 0) AS backers,
               time_created
          FROM campaigns
         WHERE creator_id = :cid
         ORDER BY amount_raised_cents DESC NULLS LAST
    """), {"cid": creator_id})
    campaigns = []
    for row in camps_r.mappings().all():
        d = dict(row)
        d["raised"] = float(d.get("raised") or 0)
        d["goal"] = float(d.get("goal") or 0)
        d["backers"] = int(d.get("backers") or 0)
        d["campaign_id"] = int(d["campaign_id"])
        d["time_created"] = d["time_created"].isoformat() if d.get("time_created") else None
        # Compute funded percentage
        d["percent_funded"] = round((d["raised"] / d["goal"]) * 100, 1) if d["goal"] > 0 else 0
        campaigns.append(d)

    # 4. Category breakdown (for pie/bar chart)
    cat_r = await db.execute(text("""
        SELECT COALESCE(category, 'Uncategorized') AS category,
               count(*) AS num_campaigns,
               COALESCE(sum(amount_raised_cents), 0) / 100.0 AS total_raised
          FROM campaigns
         WHERE creator_id = :cid
         GROUP BY category
         ORDER BY total_raised DESC
    """), {"cid": creator_id})
    category_breakdown = []
    for row in cat_r.mappings().all():
        d = dict(row)
        d["total_raised"] = float(d.get("total_raised") or 0)
        d["num_campaigns"] = int(d.get("num_campaigns") or 0)
        category_breakdown.append(d)

    # 5. Top donors (for donor list chart)
    donors_r = await db.execute(text("""
        SELECT
            COALESCE(d.donor_name, d.donor_email, 'Anonymous') AS name,
            COUNT(*) AS num_donations,
            SUM(d.amount) AS total_amount
          FROM donations d
          JOIN campaigns c ON c.campaign_id = d.campaign_id
         WHERE c.creator_id = :cid
           AND d.status = 'succeeded'
           AND COALESCE(d.is_anonymous, false) = false
         GROUP BY COALESCE(d.donor_name, d.donor_email, 'Anonymous')
         ORDER BY total_amount DESC
         LIMIT 10
    """), {"cid": creator_id})
    top_donors = []
    for row in donors_r.mappings().all():
        d = dict(row)
        d["total_amount"] = float(d.get("total_amount") or 0)
        d["num_donations"] = int(d.get("num_donations") or 0)
        top_donors.append(d)

    # 6. Recent donations (last 20)
    recent_r = await db.execute(text("""
        SELECT
            d.donation_id,
            d.campaign_id,
            c.title AS campaign_title,
            d.amount,
            d.status,
            d.time_created,
            CASE WHEN d.is_anonymous THEN 'Anonymous'
                 ELSE COALESCE(d.donor_name, d.donor_email, 'Donor')
            END AS donor_display
          FROM donations d
          JOIN campaigns c ON c.campaign_id = d.campaign_id
         WHERE c.creator_id = :cid
         ORDER BY d.time_created DESC
         LIMIT 20
    """), {"cid": creator_id})
    recent_donations = []
    for row in recent_r.mappings().all():
        d = dict(row)
        d["donation_id"] = int(d["donation_id"])
        d["campaign_id"] = int(d["campaign_id"])
        d["amount"] = float(d.get("amount") or 0)
        d["time_created"] = d["time_created"].isoformat() if d.get("time_created") else None
        recent_donations.append(d)

    # 7. Cumulative revenue over time (for area chart)
    cum_r = await db.execute(text("""
        SELECT
            d.time_created::date AS day,
            SUM(d.amount) AS daily_total
          FROM donations d
          JOIN campaigns c ON c.campaign_id = d.campaign_id
         WHERE c.creator_id = :cid
           AND d.status = 'succeeded'
         GROUP BY d.time_created::date
         ORDER BY day
    """), {"cid": creator_id})
    cumulative = []
    running = 0.0
    for row in cum_r.mappings().all():
        running += float(row.get("daily_total") or 0)
        cumulative.append({
            "day": row["day"].isoformat() if row.get("day") else None,
            "daily": float(row.get("daily_total") or 0),
            "cumulative": round(running, 2)
        })

    # 8. Recommendations (data-driven)
    recommendations = []
    if stats["total_campaigns"] == 0:
        recommendations.append({
            "type": "info",
            "title": "Create your first campaign",
            "message": "Start by creating a campaign to track its performance here."
        })
    else:
        # Recommendation: campaigns at low funding %
        low = [c for c in campaigns if c["percent_funded"] < 25 and c["status"] == "active"]
        if low:
            recommendations.append({
                "type": "tip",
                "title": f"{len(low)} campaign(s) under 25% funded",
                "message": "Boost social media presence and add video content to gain traction."
            })
        # Recommendation: top performer
        if campaigns and campaigns[0]["raised"] > 0:
            top = campaigns[0]
            recommendations.append({
                "type": "celebrate",
                "title": f"\"{top['title']}\" is your top performer",
                "message": f"${top['raised']:,.0f} raised from {top['backers']} backers ({top['percent_funded']}% of goal)."
            })
        # Recommendation: best category
        if category_breakdown and category_breakdown[0]["total_raised"] > 0:
            recommendations.append({
                "type": "insight",
                "title": f"Best category: {category_breakdown[0]['category']}",
                "message": f"${category_breakdown[0]['total_raised']:,.0f} across {category_breakdown[0]['num_campaigns']} campaign(s)."
            })

    return {
        "creator": {
            "creator_id": creator["creator_id"],
            "name": creator["name"],
            "last_name": creator["last_name"],
            "user_type": creator["user_type"],
            "is_business": creator["user_type"] == 1,
        },
        "stats": stats,
        "campaigns": campaigns,
        "category_breakdown": category_breakdown,
        "top_donors": top_donors,
        "recent_donations": recent_donations,
        "cumulative_revenue": cumulative,
        "recommendations": recommendations,
    }
