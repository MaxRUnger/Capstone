-- Optional: run once in Supabase SQL editor (or psql) so instructor-added
-- student emails persist on public.profiles and load in the UI.
ALTER TABLE public.profiles
  ADD COLUMN IF NOT EXISTS email text;

COMMENT ON COLUMN public.profiles.email IS 'School/contact email for roster students (instructor-entered).';
