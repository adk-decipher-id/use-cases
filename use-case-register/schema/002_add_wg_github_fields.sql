-- Structural change only (no data): add columns so a Working Group's
-- GitHub label and notification contact can be recorded. The actual
-- values live in curation/reference-data.yaml, loaded by
-- scripts/load_reference_data.py -- this migration only adds the columns
-- for it to populate. See docs/design.md, "Reference data vs. schema."

ALTER TABLE working_groups ADD COLUMN github_label TEXT;
ALTER TABLE working_groups ADD COLUMN github_contact TEXT;
