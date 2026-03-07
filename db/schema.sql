-- ============================================================
-- Community Fundings — Full PostgreSQL Schema (DDL)
-- Includes:
--   creators, organization_members, campaigns, donations, payments,
--   fees, payouts, refunds, saved_campaigns, reports,
--   campaign_photos, comments,
--   interests + creator_interests,
--   campaign_types + campaign_type_map
--
-- Notes (per your decisions):
--   - No trigger enforcing org/user types in organization_members
--   - payments.donation_id is OPTIONAL and NOT UNIQUE
-- ============================================================

BEGIN;

-- ----------------------------
-- 1) Creators (users + orgs)
-- ----------------------------
CREATE TABLE IF NOT EXISTS creators (
  creator_id     BIGSERIAL PRIMARY KEY,
  user_type      SMALLINT NOT NULL CHECK (user_type IN (0, 1)), -- 0=organization, 1=user
  name           TEXT NOT NULL,
  last_name      TEXT,
  email          TEXT,
  time_creation  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS creators_email_unique
  ON creators (email)
  WHERE email IS NOT NULL;

-- -----------------------------------
-- 2) Organization Members (bridge)
-- -----------------------------------
CREATE TABLE IF NOT EXISTS organization_members (
  member_id        BIGINT NOT NULL,
  organization_id  BIGINT NOT NULL,
  PRIMARY KEY (member_id, organization_id),
  CONSTRAINT fk_org_members_member
    FOREIGN KEY (member_id) REFERENCES creators (creator_id)
    ON DELETE CASCADE,
  CONSTRAINT fk_org_members_org
    FOREIGN KEY (organization_id) REFERENCES creators (creator_id)
    ON DELETE CASCADE,
  CONSTRAINT chk_no_self_membership
    CHECK (member_id <> organization_id)
);

CREATE INDEX IF NOT EXISTS idx_org_members_org
  ON organization_members (organization_id);

CREATE INDEX IF NOT EXISTS idx_org_members_member
  ON organization_members (member_id);

-- ----------------------------
-- 3) Campaigns
-- ----------------------------
CREATE TABLE IF NOT EXISTS campaigns (
  campaign_id   BIGSERIAL PRIMARY KEY,
  creator_id    BIGINT NOT NULL,     -- campaign owner (user or org)
  title         TEXT NOT NULL,
  status        TEXT NOT NULL,       -- e.g., 'active', 'closed'
  time_created  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT fk_campaigns_creator
    FOREIGN KEY (creator_id) REFERENCES creators (creator_id)
    ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_campaigns_creator
  ON campaigns (creator_id);

CREATE INDEX IF NOT EXISTS idx_campaigns_status
  ON campaigns (status);

-- ----------------------------
-- 4) Donations
-- ----------------------------
CREATE TABLE IF NOT EXISTS donations (
  donation_id       BIGSERIAL PRIMARY KEY,
  campaign_id       BIGINT NOT NULL,
  donor_creator_id  BIGINT NOT NULL, -- donor (should be user_type=1 in app logic)
  amount            NUMERIC(12,2) NOT NULL CHECK (amount > 0),
  status            TEXT NOT NULL,   -- 'paid'/'failed'/'refunded'
  time_created      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT fk_donations_campaign
    FOREIGN KEY (campaign_id) REFERENCES campaigns (campaign_id)
    ON DELETE CASCADE,
  CONSTRAINT fk_donations_donor
    FOREIGN KEY (donor_creator_id) REFERENCES creators (creator_id)
    ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_donations_campaign
  ON donations (campaign_id);

CREATE INDEX IF NOT EXISTS idx_donations_donor
  ON donations (donor_creator_id);

CREATE INDEX IF NOT EXISTS idx_donations_status
  ON donations (status);

-- ----------------------------
-- 5) Payments (Stripe)
-- ----------------------------
CREATE TABLE IF NOT EXISTS payments (
  payment_id     BIGSERIAL PRIMARY KEY,
  donation_id    BIGINT, -- optional, non-unique
  processor      TEXT NOT NULL DEFAULT 'stripe',
  status         TEXT NOT NULL, -- 'authorized'/'captured'/'failed'
  time_captured  TIMESTAMPTZ,
  time_settled   TIMESTAMPTZ,
  time_created   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT fk_payments_donation
    FOREIGN KEY (donation_id) REFERENCES donations (donation_id)
    ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_payments_donation
  ON payments (donation_id);

CREATE INDEX IF NOT EXISTS idx_payments_status
  ON payments (status);

-- ----------------------------
-- 6) Fees
-- ----------------------------
CREATE TABLE IF NOT EXISTS fees (
  fee_id        BIGSERIAL PRIMARY KEY,
  campaign_id   BIGINT NOT NULL,
  donation_id   BIGINT NOT NULL,
  amount        NUMERIC(12,2) NOT NULL CHECK (amount >= 0),
  time_created  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT fk_fees_campaign
    FOREIGN KEY (campaign_id) REFERENCES campaigns (campaign_id)
    ON DELETE CASCADE,
  CONSTRAINT fk_fees_donation
    FOREIGN KEY (donation_id) REFERENCES donations (donation_id)
    ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_fees_campaign
  ON fees (campaign_id);

CREATE INDEX IF NOT EXISTS idx_fees_donation
  ON fees (donation_id);

-- ----------------------------
-- 7) Payouts
-- ----------------------------
CREATE TABLE IF NOT EXISTS payouts (
  payout_id        BIGSERIAL PRIMARY KEY,
  campaign_id      BIGINT NOT NULL,
  payee_creator_id BIGINT NOT NULL, -- payout recipient (user or org)
  amount           NUMERIC(12,2) NOT NULL CHECK (amount > 0),
  time_initiated   TIMESTAMPTZ,
  time_paid        TIMESTAMPTZ,
  CONSTRAINT fk_payouts_campaign
    FOREIGN KEY (campaign_id) REFERENCES campaigns (campaign_id)
    ON DELETE CASCADE,
  CONSTRAINT fk_payouts_payee
    FOREIGN KEY (payee_creator_id) REFERENCES creators (creator_id)
    ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_payouts_campaign
  ON payouts (campaign_id);

CREATE INDEX IF NOT EXISTS idx_payouts_payee
  ON payouts (payee_creator_id);

-- ----------------------------
-- 8) Refunds
-- ----------------------------
CREATE TABLE IF NOT EXISTS refunds (
  refund_id       BIGSERIAL PRIMARY KEY,
  donation_id     BIGINT NOT NULL,
  payment_id      BIGINT NOT NULL,
  amount          NUMERIC(12,2) NOT NULL CHECK (amount > 0),
  status          TEXT NOT NULL, -- 'initiated'/'paid'/'failed'
  time_initiated  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  time_paid       TIMESTAMPTZ,
  CONSTRAINT fk_refunds_donation
    FOREIGN KEY (donation_id) REFERENCES donations (donation_id)
    ON DELETE CASCADE,
  CONSTRAINT fk_refunds_payment
    FOREIGN KEY (payment_id) REFERENCES payments (payment_id)
    ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_refunds_donation
  ON refunds (donation_id);

CREATE INDEX IF NOT EXISTS idx_refunds_payment
  ON refunds (payment_id);

CREATE INDEX IF NOT EXISTS idx_refunds_status
  ON refunds (status);

-- ----------------------------
-- 9) Saved Campaigns (history/bookmark)
-- ----------------------------
CREATE TABLE IF NOT EXISTS saved_campaigns (
  creator_id       BIGINT NOT NULL, -- saver (should be user_type=1 in app logic)
  campaign_id      BIGINT NOT NULL,
  engagement_type  SMALLINT NOT NULL CHECK (engagement_type IN (0, 1)), -- 0=history, 1=bookmark
  time_created     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (creator_id, campaign_id, engagement_type),
  CONSTRAINT fk_saved_creator
    FOREIGN KEY (creator_id) REFERENCES creators (creator_id)
    ON DELETE CASCADE,
  CONSTRAINT fk_saved_campaign
    FOREIGN KEY (campaign_id) REFERENCES campaigns (campaign_id)
    ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_saved_campaigns_campaign
  ON saved_campaigns (campaign_id);

-- ----------------------------
-- 10) Reports
-- ----------------------------
CREATE TABLE IF NOT EXISTS reports (
  report_id     BIGSERIAL PRIMARY KEY,
  reporter_id   BIGINT NOT NULL, -- creator filing report (should be user_type=1 in app logic)
  campaign_id   BIGINT NOT NULL,
  strength_id   BIGINT,          -- placeholder for weighting system
  time_created  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT fk_reports_reporter
    FOREIGN KEY (reporter_id) REFERENCES creators (creator_id)
    ON DELETE CASCADE,
  CONSTRAINT fk_reports_campaign
    FOREIGN KEY (campaign_id) REFERENCES campaigns (campaign_id)
    ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_reports_campaign
  ON reports (campaign_id);

CREATE INDEX IF NOT EXISTS idx_reports_reporter
  ON reports (reporter_id);

-- ----------------------------
-- 11) Campaign Photos (S3 metadata)
-- ----------------------------
CREATE TABLE IF NOT EXISTS campaign_photos (
  photo_id        BIGSERIAL PRIMARY KEY,
  campaign_id     BIGINT NOT NULL,
  s3_bucket       TEXT NOT NULL,
  s3_key          TEXT NOT NULL,
  content_type    TEXT,
  file_size_bytes BIGINT CHECK (file_size_bytes >= 0),
  width_px        INT CHECK (width_px >= 0),
  height_px       INT CHECK (height_px >= 0),
  is_primary      BOOLEAN NOT NULL DEFAULT FALSE,
  sort_order      INT NOT NULL DEFAULT 0,
  uploaded_by_creator_id BIGINT,
  time_created    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT fk_campaign_photos_campaign
    FOREIGN KEY (campaign_id) REFERENCES campaigns (campaign_id)
    ON DELETE CASCADE,
  CONSTRAINT fk_campaign_photos_uploader
    FOREIGN KEY (uploaded_by_creator_id) REFERENCES creators (creator_id)
    ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_campaign_photos_campaign
  ON campaign_photos (campaign_id);

CREATE INDEX IF NOT EXISTS idx_campaign_photos_primary
  ON campaign_photos (campaign_id, is_primary);

CREATE UNIQUE INDEX IF NOT EXISTS uq_campaign_photos_s3_object
  ON campaign_photos (s3_bucket, s3_key);

-- Optional (recommended) if you want only one primary image per campaign:
-- CREATE UNIQUE INDEX IF NOT EXISTS uq_one_primary_photo_per_campaign
--   ON campaign_photos (campaign_id)
--   WHERE is_primary = TRUE;

-- ----------------------------
-- 12) Comments
-- ----------------------------
CREATE TABLE IF NOT EXISTS comments (
  comment_id     BIGSERIAL PRIMARY KEY,
  comment_text   TEXT NOT NULL CHECK (length(trim(comment_text)) > 0),
  creator_id     BIGINT NOT NULL,
  campaign_id    BIGINT NOT NULL,
  time_created   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT fk_comments_creator
    FOREIGN KEY (creator_id) REFERENCES creators (creator_id)
    ON DELETE CASCADE,
  CONSTRAINT fk_comments_campaign
    FOREIGN KEY (campaign_id) REFERENCES campaigns (campaign_id)
    ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_comments_campaign
  ON comments (campaign_id);

CREATE INDEX IF NOT EXISTS idx_comments_creator
  ON comments (creator_id);

CREATE INDEX IF NOT EXISTS idx_comments_time
  ON comments (campaign_id, time_created DESC);

-- ----------------------------
-- 13) Interests + Creator ↔ Interests bridge
-- ----------------------------
CREATE TABLE IF NOT EXISTS interests (
  interest_id   BIGSERIAL PRIMARY KEY,
  name          TEXT NOT NULL UNIQUE,
  time_created  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS creator_interests (
  creator_id   BIGINT NOT NULL,
  interest_id  BIGINT NOT NULL,
  PRIMARY KEY (creator_id, interest_id),
  CONSTRAINT fk_creator_interests_creator
    FOREIGN KEY (creator_id) REFERENCES creators (creator_id)
    ON DELETE CASCADE,
  CONSTRAINT fk_creator_interests_interest
    FOREIGN KEY (interest_id) REFERENCES interests (interest_id)
    ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_creator_interests_interest
  ON creator_interests (interest_id);

-- ----------------------------
-- 14) Campaign Types + Campaign ↔ Types bridge
-- ----------------------------
CREATE TABLE IF NOT EXISTS campaign_types (
  type_id       BIGSERIAL PRIMARY KEY,
  name          TEXT NOT NULL UNIQUE,
  description   TEXT,
  time_created  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS campaign_type_map (
  campaign_id  BIGINT NOT NULL,
  type_id      BIGINT NOT NULL,
  PRIMARY KEY (campaign_id, type_id),
  CONSTRAINT fk_campaign_type_campaign
    FOREIGN KEY (campaign_id) REFERENCES campaigns (campaign_id)
    ON DELETE CASCADE,
  CONSTRAINT fk_campaign_type_type
    FOREIGN KEY (type_id) REFERENCES campaign_types (type_id)
    ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_campaign_type_map_type
  ON campaign_type_map (type_id);

COMMIT;
