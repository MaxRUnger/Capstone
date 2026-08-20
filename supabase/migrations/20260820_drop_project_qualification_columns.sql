-- Drop Qualification/piece-era columns that the app no longer reads or writes.
-- Keep assignments.assignment_type: 'project' remains a label like 'exam'.
--
-- Run in the Supabase SQL editor (or via supabase db push). Safe to re-run:
-- each DROP uses IF EXISTS. Postgres also drops FKs and indexes on these columns.

ALTER TABLE public.assignments
  DROP COLUMN IF EXISTS parent_project_id,
  DROP COLUMN IF EXISTS required_total_masteries;

ALTER TABLE public.assignment_objectives
  DROP COLUMN IF EXISTS required_ms_for_project,
  DROP COLUMN IF EXISTS source_assignment_id;

ALTER TABLE public.learning_objectives
  DROP COLUMN IF EXISTS owner_qualification_id,
  DROP COLUMN IF EXISTS owner_project_id;
