-- Run on public.bank_details after 001_bank_details_ciphertext.sql (if you used it).
-- Drops unused account_number (ciphertext is a single Fernet token in the other column).
-- Renames routing_number -> fermat_key (stores the encrypted JSON blob, not a literal routing #).

ALTER TABLE public.bank_details
  DROP COLUMN IF EXISTS account_number;

ALTER TABLE public.bank_details
  RENAME COLUMN routing_number TO fermat_key;
