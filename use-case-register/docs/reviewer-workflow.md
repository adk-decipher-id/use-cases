# Reviewer Workflow

Only a small reviewer/curator group does this — not every submitter, and not
every field needs to be filled in.

## Statuses

```
new           -> just synced from GitHub, untouched
needs_review  -> a reviewer has looked but not finished mapping
mapped        -> theme/WG/taxonomy links are set
wg_input      -> handed to a working group as input
parked        -> valid but not actionable right now
closed        -> done, or the underlying GitHub issue was closed as invalid
```

Set with `review_use_case.py --issue N --status <status>`.

## What to check, in order

For each use case in the queue (`review_use_case.py --list-unreviewed`):

1. Is it valid and relevant to FG-TIDA's scope?
2. Which theme or working group does it support?
   `--add-theme "Delegation"` / `--add-wg wg1-ra-aai`
3. What standards gap does it reveal, if any?
   `--add-standards-gap "..."`
4. Does it introduce or reinforce a taxonomy term?
   `--add-taxonomy-term "mandate"`
5. Is it related to an existing use case?
   `--related-to <other issue number>`

Don't feel obliged to fill in every field — map what's clear, leave the rest
for a later pass or another reviewer.

## Commands

```bash
# queues
python scripts/review_use_case.py --list-unreviewed
python scripts/review_use_case.py --list-unmapped

# full pass on one issue
python scripts/review_use_case.py --issue 12 \
    --status mapped \
    --summary "Cross-border delegation of a payment mandate" \
    --reviewer alice \
    --add-theme Delegation --add-theme "Discovery and Cross-Border Trust" \
    --add-wg wg1-cdra:primary --add-wg wg2-ibea:secondary \
    --add-taxonomy-term mandate \
    --add-standards-gap "No standard for cross-border mandate revocation propagation" \
    --related-to 7
```

`--add-theme` and `--add-taxonomy-term` create the row if it doesn't already
exist — new themes/terms are expected to emerge from real use cases.
`--add-wg` requires the working group to already exist in `working_groups`
(add it to `curation/reference-data.yaml` when FG-TIDA forms a new one, then
run `scripts/load_reference_data.py`).

#### Primary vs. secondary

`--add-wg` takes an optional `:primary` or `:secondary` suffix (e.g.
`wg1-cdra:primary`). This generalizes to any number of Working Groups —
nothing about it is specific to WG1 or WG2, so a future WG3/WG4 needs no
code changes, just an entry in `curation/reference-data.yaml`.

- **At most one WG can be primary per use case.** The CLI refuses a second
  primary in the same command, and refuses adding a new primary while a
  different one is already recorded — demote the existing one explicitly
  first (`--add-wg <existing-slug>:secondary`).
- Omitting the role (`--add-wg wg1-cdra`, no suffix) never erases a role
  set earlier — only an explicit `:primary`/`:secondary` ever changes one.
- If a use case is tagged first with no role and given one later, the
  GitHub label is kept in sync either way (the earlier, unsuffixed label
  is removed and replaced) — this isn't limited to the moment of first
  tagging.

### Notifying the tagged Working Group

Tagging a use case to a WG with `--add-wg` only changes WG0's own data unless
the WG actually finds out — a label buried in a committed YAML file isn't
visible to anyone who isn't already looking for it. So the first time a
use case is linked to a given WG (never on a re-run of the same command),
the CLI:

1. **Automatically** adds that WG's GitHub label (e.g. `wg1-cdra`) to the
   issue itself, so it's a plain, bookmarkable GitHub search for anyone —
   `is:issue label:wg1-cdra` in `fg-tida-wg0/use-cases`. Pure
   categorization, not a public statement, so this step needs no review.
2. **Prints a draft** of a notification comment that @-mentions that WG's
   recorded `github_contact` (from `curation/reference-data.yaml`) — but
   does **not** post it. @-mentioning someone is a public statement
   attributed to the reviewer's account, so it must be read and approved
   by a human before it goes anywhere. The exact `gh issue comment` command
   to run is printed alongside the draft; posting it is a separate,
   deliberate action you take after reviewing the wording.

Both depend on `github_label`/`github_contact` being set for that WG in
`curation/reference-data.yaml`; a WG with neither set gets a warning printed
instead of a silent no-op.

## After running the CLI

Each update also rewrites `curation/mappings.yaml` — that file, not the
local sqlite db, is what other reviewers and the dashboard's daily rebuild
actually see. Commit and push it (or open a small PR) after a review pass:

```bash
git add curation/mappings.yaml
git commit -m "Review issue #12"
git push
```

The dashboard (`docs/dashboard.md`) picks up committed changes on its next
scheduled or manually triggered run.
