-- REQUIRED once: encrypted bank details store a Fernet token (~200+ chars) in routing_number
-- (before migration 002 renames it to fermat_key).
-- Without this you get: "value too long for type character varying(9)"
--
-- Run against your Postgres (psql, RDS Query Editor, etc.):

ALTER TABLE public.bank_details
  ALTER COLUMN routing_number TYPE TEXT,
  ALTER COLUMN account_number TYPE TEXT;

-- Then run 002_bank_details_drop_account_rename_routing.sql to drop account_number and rename column.
