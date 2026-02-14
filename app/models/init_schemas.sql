-- ============================================================================
-- Community Fundings — Complete Database Schema
-- Run this against your PostgreSQL RDS instance to initialize all tables
-- ============================================================================

CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(50) PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    avatar_url VARCHAR(500),
    bio TEXT,
    email_verified BOOLEAN DEFAULT FALSE,
    is_admin BOOLEAN DEFAULT FALSE,
    stripe_customer_id VARCHAR(255) UNIQUE,
    stripe_connect_account_id VARCHAR(255) UNIQUE,
    stripe_connect_onboarded BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

CREATE TABLE IF NOT EXISTS campaigns (
    id VARCHAR(50) PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    slug VARCHAR(220) UNIQUE NOT NULL,
    short_description VARCHAR(300),
    description TEXT NOT NULL,
    goal_amount NUMERIC(12,2) NOT NULL,
    raised_amount NUMERIC(12,2) DEFAULT 0.00,
    image_url VARCHAR(500),
    video_url VARCHAR(500),
    creator_id VARCHAR(50) NOT NULL REFERENCES users(id),
    status VARCHAR(20) NOT NULL DEFAULT 'draft',
    donors_count INTEGER DEFAULT 0,
    category VARCHAR(100),
    location VARCHAR(255),
    end_date TIMESTAMPTZ,
    is_featured BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_campaigns_slug ON campaigns(slug);
CREATE INDEX IF NOT EXISTS idx_campaigns_status_created ON campaigns(status, created_at);
CREATE INDEX IF NOT EXISTS idx_campaigns_category ON campaigns(category);
CREATE INDEX IF NOT EXISTS idx_campaigns_creator ON campaigns(creator_id);

CREATE TABLE IF NOT EXISTS donations (
    id VARCHAR(50) PRIMARY KEY,
    campaign_id VARCHAR(50) NOT NULL REFERENCES campaigns(id),
    donor_id VARCHAR(50) REFERENCES users(id),
    donor_name VARCHAR(255) DEFAULT 'Anonymous',
    donor_email VARCHAR(255),
    is_anonymous BOOLEAN DEFAULT FALSE,
    message TEXT,
    amount NUMERIC(12,2) NOT NULL,
    platform_fee NUMERIC(12,2),
    processing_fee NUMERIC(12,2),
    net_amount NUMERIC(12,2),
    currency VARCHAR(3) DEFAULT 'usd',
    stripe_payment_intent_id VARCHAR(255) UNIQUE,
    stripe_checkout_session_id VARCHAR(255) UNIQUE,
    stripe_charge_id VARCHAR(255),
    refund_amount NUMERIC(12,2),
    stripe_refund_id VARCHAR(255),
    status VARCHAR(30) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_donations_campaign_status ON donations(campaign_id, status);
CREATE INDEX IF NOT EXISTS idx_donations_donor ON donations(donor_id);
CREATE INDEX IF NOT EXISTS idx_donations_pi ON donations(stripe_payment_intent_id);
CREATE INDEX IF NOT EXISTS idx_donations_session ON donations(stripe_checkout_session_id);

CREATE TABLE IF NOT EXISTS refund_requests (
    id VARCHAR(50) PRIMARY KEY,
    donation_id VARCHAR(50) NOT NULL REFERENCES donations(id),
    requester_id VARCHAR(50) NOT NULL REFERENCES users(id),
    reason TEXT NOT NULL,
    amount NUMERIC(12,2),
    status VARCHAR(20) NOT NULL DEFAULT 'requested',
    admin_notes TEXT,
    stripe_refund_id VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS payouts (
    id VARCHAR(50) PRIMARY KEY,
    campaign_id VARCHAR(50) NOT NULL REFERENCES campaigns(id),
    creator_id VARCHAR(50) NOT NULL REFERENCES users(id),
    amount NUMERIC(12,2) NOT NULL,
    currency VARCHAR(3) DEFAULT 'usd',
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    stripe_transfer_id VARCHAR(255),
    stripe_payout_id VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS campaign_updates (
    id VARCHAR(50) PRIMARY KEY,
    campaign_id VARCHAR(50) NOT NULL REFERENCES campaigns(id),
    author_id VARCHAR(50) NOT NULL REFERENCES users(id),
    title VARCHAR(200) NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_updates_campaign ON campaign_updates(campaign_id);

CREATE TABLE IF NOT EXISTS comments (
    id VARCHAR(50) PRIMARY KEY,
    campaign_id VARCHAR(50) NOT NULL REFERENCES campaigns(id),
    user_id VARCHAR(50) NOT NULL REFERENCES users(id),
    content TEXT NOT NULL,
    is_hidden BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_comments_campaign ON comments(campaign_id);

CREATE TABLE IF NOT EXISTS reports (
    id VARCHAR(50) PRIMARY KEY,
    reporter_id VARCHAR(50) NOT NULL REFERENCES users(id),
    campaign_id VARCHAR(50) REFERENCES campaigns(id),
    comment_id VARCHAR(50) REFERENCES comments(id),
    reason VARCHAR(30) NOT NULL,
    details TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'open',
    admin_notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS payment_details (
    id VARCHAR(50) PRIMARY KEY,
    user_id VARCHAR(50) NOT NULL REFERENCES users(id),
    account_type VARCHAR(20) DEFAULT 'individual',
    account_holder_name VARCHAR(255) NOT NULL,
    routing_number_last4 VARCHAR(4),
    account_number_last4 VARCHAR(4),
    stripe_connect_account_id VARCHAR(255),
    is_verified BOOLEAN DEFAULT FALSE,
    is_default BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS billing_addresses (
    id VARCHAR(50) PRIMARY KEY,
    user_id VARCHAR(50) NOT NULL REFERENCES users(id),
    full_name VARCHAR(255) NOT NULL,
    address_line1 VARCHAR(255) NOT NULL,
    address_line2 VARCHAR(255),
    city VARCHAR(100) NOT NULL,
    state VARCHAR(100),
    postal_code VARCHAR(20) NOT NULL,
    country VARCHAR(2) DEFAULT 'US',
    is_default BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS webhook_events (
    id VARCHAR(50) PRIMARY KEY,
    stripe_event_id VARCHAR(255) UNIQUE NOT NULL,
    event_type VARCHAR(100) NOT NULL,
    payload TEXT,
    processed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_webhook_stripe_id ON webhook_events(stripe_event_id);