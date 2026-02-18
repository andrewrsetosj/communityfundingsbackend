# Community Fundings — Database Schema (PostgreSQL)

This directory contains the PostgreSQL schema for the Community Fundings backend.

The schema models a crowdfunding platform supporting:

- Users and organizations
- Campaign creation
- Donations and payments (Stripe)
- Fees, payouts, refunds
- Engagement (saved campaigns, comments, reports)
- Media (campaign photos via S3)
- Categorization (interests and campaign types)

The schema is defined in:

Tables
1. creators

Canonical identity table for both users and organizations.

Column	Type	Notes
creator_id	BIGSERIAL PK	Unique creator ID
user_type	SMALLINT	0=organization, 1=user
name	TEXT	Display name
last_name	TEXT	Optional (users)
email	TEXT	Optional
time_creation	TIMESTAMPTZ	Created timestamp
Constraints

CHECK (user_type IN (0,1))

Indexes

Unique email only when not null

2. organization_members

Many-to-many membership between creators.

Represents users belonging to organizations.

Column	Type
member_id	FK → creators
organization_id	FK → creators

Composite PK: (member_id, organization_id)

Constraints

No self-membership

Cascade delete on both FKs

3. campaigns

Campaigns created by a creator (user or organization).

Column	Type
campaign_id	PK
creator_id	FK → creators
title	TEXT
status	TEXT
time_created	TIMESTAMPTZ
Delete behavior

Creator delete: RESTRICT

Campaign delete: cascades downstream (donations, etc.)

4. donations

User donations to campaigns.

Column	Type
donation_id	PK
campaign_id	FK → campaigns
donor_creator_id	FK → creators
amount	NUMERIC(12,2)
status	TEXT
time_created	TIMESTAMPTZ
Notes

Donor should be user_type = 1 (app logic)

Campaign delete cascades

Donor delete restricted

5. payments

Payment processor records (Stripe).

Column	Type
payment_id	PK
donation_id	FK → donations (optional)
processor	TEXT
status	TEXT
time_captured	TIMESTAMPTZ
time_settled	TIMESTAMPTZ
time_created	TIMESTAMPTZ
Design decision

donation_id is optional

not unique

allows retries / multiple attempts

6. fees

Platform fees collected per donation.

Column	Type
fee_id	PK
campaign_id	FK → campaigns
donation_id	FK → donations
amount	NUMERIC
time_created	TIMESTAMPTZ

Campaign + donation cascade on delete.

7. payouts

Funds paid out to campaign owner.

Column	Type
payout_id	PK
campaign_id	FK → campaigns
payee_creator_id	FK → creators
amount	NUMERIC
time_initiated	TIMESTAMPTZ
time_paid	TIMESTAMPTZ

Payee delete restricted.

8. refunds

Refunds issued for donations.

Column	Type
refund_id	PK
donation_id	FK → donations
payment_id	FK → payments
amount	NUMERIC
status	TEXT
time_initiated	TIMESTAMPTZ
time_paid	TIMESTAMPTZ

Donation delete cascades.
Payment delete restricted.

9. saved_campaigns

Campaign engagement history and bookmarks.

Column	Type
creator_id	FK → creators
campaign_id	FK → campaigns
engagement_type	SMALLINT
time_created	TIMESTAMPTZ

Composite PK:

(creator_id, campaign_id, engagement_type)


Engagement types:

0 = history

1 = bookmark

10. reports

User reports against campaigns.

Column	Type
report_id	PK
reporter_id	FK → creators
campaign_id	FK → campaigns
strength_id	BIGINT (future weighting)
time_created	TIMESTAMPTZ

Cascade on reporter and campaign delete.

11. campaign_photos

Metadata for S3-stored campaign images.

Column	Type
photo_id	PK
campaign_id	FK → campaigns
s3_bucket	TEXT
s3_key	TEXT
content_type	TEXT
file_size_bytes	BIGINT
width_px	INT
height_px	INT
is_primary	BOOLEAN
sort_order	INT
uploaded_by_creator_id	FK → creators
time_created	TIMESTAMPTZ
Indexes

campaign lookup

primary image lookup

unique S3 object

Optional unique constraint available for single primary photo.

12. comments

Comments on campaigns.

Column	Type
comment_id	PK
comment_text	TEXT
creator_id	FK → creators
campaign_id	FK → campaigns
time_created	TIMESTAMPTZ

Cascade on creator and campaign delete.

13. interests

Interest taxonomy.

Column	Type
interest_id	PK
name	UNIQUE
time_created	TIMESTAMPTZ
14. creator_interests

Many-to-many creators ↔ interests.

Composite PK:

(creator_id, interest_id)


Cascade delete on both sides.

15. campaign_types

Campaign category taxonomy.

Column	Type
type_id	PK
name	UNIQUE
description	TEXT
time_created	TIMESTAMPTZ
16. campaign_type_map

Many-to-many campaigns ↔ types.

Composite PK:

(campaign_id, type_id)


Cascade delete on both sides.

Relationship Summary

creators → campaigns (1:N)

creators → donations (1:N)

campaigns → donations (1:N)

donations → payments (1:N optional)

campaigns → payouts (1:N)

donations → refunds (1:N)

campaigns → photos/comments/reports (1:N)

creators ↔ organizations (M:N via organization_members)

creators ↔ interests (M:N)

campaigns ↔ types (M:N)