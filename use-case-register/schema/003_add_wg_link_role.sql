-- Structural change only (no data): record whether a use case's link to a
-- Working Group is primary or secondary. Previously this distinction only
-- existed as free-text in review_notes (see use case #35's review notes,
-- which name this exact gap) because use_case_working_group_links was an
-- unordered link table with no way to express it. Generalizes to any
-- number of future Working Groups -- nothing here names WG1 or WG2
-- specifically.

ALTER TABLE use_case_working_group_links
    ADD COLUMN role TEXT CHECK (role IN ('primary', 'secondary') OR role IS NULL);
