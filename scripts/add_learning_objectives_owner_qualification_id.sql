-- Qualification "pieces" are private, single-use learning_objectives rows
-- owned by one Qualification (assignments.id). owner_qualification_id NULL
-- means a normal class LO (shared pool). ON DELETE CASCADE removes a
-- Qualification's pieces when the Qualification itself is deleted.

ALTER TABLE learning_objectives
  ADD COLUMN IF NOT EXISTS owner_qualification_id uuid NULL
  REFERENCES assignments(id) ON DELETE CASCADE;
