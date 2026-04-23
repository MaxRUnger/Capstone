-- Run once in Supabase SQL editor (or psql).
--
-- Goal:
-- 1) Backfill description from legacy name when description is blank.
-- 2) Ensure vendor_code is the required identifier for objectives.
-- 3) Make legacy name optional (UI/backend no longer relies on it).
--
-- Rollback guidance:
-- - Re-add NOT NULL on name only if your old app version still requires it:
--     ALTER TABLE public.learning_objectives ALTER COLUMN name SET NOT NULL;
-- - Drop the non-empty vendor_code constraint if needed:
--     ALTER TABLE public.learning_objectives DROP CONSTRAINT IF EXISTS learning_objectives_vendor_code_nonempty;

ALTER TABLE public.learning_objectives
  ADD COLUMN IF NOT EXISTS description text;

-- Preserve old objective names inside description where description was never set.
UPDATE public.learning_objectives
SET description = NULLIF(BTRIM(name), '')
WHERE (description IS NULL OR BTRIM(description) = '')
  AND name IS NOT NULL
  AND BTRIM(name) <> '';

-- If vendor_code is missing on legacy rows, copy it from the old name when possible.
UPDATE public.learning_objectives
SET vendor_code = NULLIF(BTRIM(name), '')
WHERE (vendor_code IS NULL OR BTRIM(vendor_code) = '')
  AND name IS NOT NULL
  AND BTRIM(name) <> '';

-- name is now optional.
ALTER TABLE public.learning_objectives
  ALTER COLUMN name DROP NOT NULL;

-- Enforce non-empty vendor_code for future writes.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'learning_objectives_vendor_code_nonempty'
  ) THEN
    ALTER TABLE public.learning_objectives
      ADD CONSTRAINT learning_objectives_vendor_code_nonempty
      CHECK (BTRIM(vendor_code) <> '');
  END IF;
END
$$;

ALTER TABLE public.learning_objectives
  ALTER COLUMN vendor_code SET NOT NULL;
