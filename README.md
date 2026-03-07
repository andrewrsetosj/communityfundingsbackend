# Community Fundings Backend (minimal)

Serves **health** and **POST /api/campaigns/finalize** for the create-project payment page. No ORM, no table creation — uses your existing PostgreSQL schema.

## Run

```bash
cd backend
source .venv/bin/activate   # or: .venv\Scripts\activate on Windows
uvicorn main:app --reload --env-file .env --port 4000
```

Or: `python main.py` (uses `PORT` from env, default 4000).

## Environment

- **DATABASE_URL** — PostgreSQL URL, e.g. `postgresql://user:pass@host:5432/cf-db` (the last segment is the database name; use `cf-db` or your DB name).
- **PORT** — Server port (default 4000).
- **FRONTEND_URL** — Allowed CORS origin (default `http://localhost:3000`).

## Database

You must have:

1. **public.campaigns** — with columns: `campaign_id` (bigserial), `creator_id` (text), `title`, `status`, `time_created`, `url`, `updated_at`, `description`, `category`, `location`, `funding_goal_cents`, `duration_days`, `amount_raised_cents`, `backers`, `end_date`. Unique on `url`.
2. **public.creators** — at least `creator_id` (PK). Every `campaigns.creator_id` must exist in `creators.creator_id`.

The frontend draft sends `creator_id` (e.g. `"creator_001"`). Ensure that value exists in `creators` before submitting, or add a row there.

## Endpoints

- `GET /` — Health.
- `GET /api/config` — Stripe/config for frontend.
- `POST /api/campaigns/finalize` — Body: full create-project draft JSON. Returns `{ "campaign_id": <int>, "slug": "<url>" }`.
