#!/usr/bin/env python3
"""Reviewer CLI: curate a use case's status, notes, and mappings.

Stands in for a dashboard mapping UI until one exists. Every update is
applied to the local sqlite cache AND written back to
curation/mappings.yaml, which is the durable, git-committed source of
truth for curated data (see docs/design.md). Commit that file after
reviewing so CI's dashboard build picks up your changes.

See docs/reviewer-workflow.md for the review process this supports.

Examples:
  python scripts/review_use_case.py --list-unmapped
  python scripts/review_use_case.py --list-unreviewed
  python scripts/review_use_case.py --issue 12 --status mapped \\
      --summary "Cross-border delegation of a payment mandate" \\
      --reviewer alice --add-theme Delegation \\
      --add-wg wg1-cdra:primary --add-wg wg2-ibea:secondary
  python scripts/review_use_case.py --issue 12 --add-taxonomy-term "mandate"
  python scripts/review_use_case.py --issue 12 --add-standards-gap \\
      "No standard for cross-border mandate revocation propagation"
  python scripts/review_use_case.py --issue 12 --related-to 7
"""

import argparse
import os
import sqlite3
import subprocess
import sys

import _curation as curation
from _env import db_path, load_env

REVIEW_STATUSES = {"new", "needs_review", "mapped", "wg_input", "parked", "closed"}


def list_query(conn, description, sql):
    rows = conn.execute(sql).fetchall()
    print(f"{description} ({len(rows)}):")
    for issue_number, title in rows:
        print(f"  #{issue_number}\t{title}")


def read_current_entry(conn, issue_number):
    """Current curated state for an issue, in curation/mappings.yaml shape."""
    row = conn.execute(
        """SELECT id, review_status, summary, review_notes, priority, reviewer, reviewed_at
           FROM use_cases WHERE github_issue_number = ?""",
        (issue_number,),
    ).fetchone()
    use_case_id = row[0]
    entry = {
        "review_status": row[1],
        "summary": row[2],
        "review_notes": row[3],
        "priority": row[4],
        "reviewer": row[5],
        "reviewed_at": row[6],
    }

    entry["themes"] = [r[0] for r in conn.execute(
        """SELECT t.name FROM use_case_theme_links l JOIN themes t ON t.id = l.theme_id
           WHERE l.use_case_id = ? ORDER BY t.name""", (use_case_id,)).fetchall()]
    wg_rows = conn.execute(
        """SELECT w.slug, l.role FROM use_case_working_group_links l
           JOIN working_groups w ON w.id = l.working_group_id
           WHERE l.use_case_id = ? ORDER BY w.slug""", (use_case_id,)).fetchall()
    entry["working_groups"] = [slug for slug, _role in wg_rows]
    entry["working_group_roles"] = {slug: role for slug, role in wg_rows if role is not None}
    entry["taxonomy_terms"] = [r[0] for r in conn.execute(
        """SELECT x.term FROM use_case_taxonomy_links l JOIN taxonomy_terms x ON x.id = l.taxonomy_term_id
           WHERE l.use_case_id = ? ORDER BY x.term""", (use_case_id,)).fetchall()]
    entry["standards_gaps"] = [r[0] for r in conn.execute(
        """SELECT g.title FROM use_case_standards_gap_links l JOIN standards_gaps g ON g.id = l.standards_gap_id
           WHERE l.use_case_id = ? ORDER BY g.title""", (use_case_id,)).fetchall()]
    entry["related_use_cases"] = [r[0] for r in conn.execute(
        """SELECT uc.github_issue_number FROM related_use_case_links l
           JOIN use_cases uc ON uc.id = CASE WHEN l.use_case_id_a = ? THEN l.use_case_id_b ELSE l.use_case_id_a END
           WHERE l.use_case_id_a = ? OR l.use_case_id_b = ?
           ORDER BY uc.github_issue_number""",
        (use_case_id, use_case_id, use_case_id)).fetchall()]

    return entry


def sync_wg_labels(issue_number, label_syncs):
    """Apply role-qualified WG labels (e.g. 'wg1-cdra:primary') on GitHub so
    they match the register's current state. Pure categorization, not a
    public statement, so this runs automatically with no review step --
    unlike the notification comment (see draft_wg_notifications below).

    label_syncs: list of (old_label_to_remove_or_None, new_label_to_add).
    Called for every --add-wg processed, not just newly-added links, since
    a role can change on a WG that was already linked (tagged with no role
    first, given one later) -- the label needs to track that too.

    Failures are reported but never abort the review -- the curated data
    is already saved by this point."""
    if not label_syncs:
        return
    load_env()
    repo = os.environ.get("GITHUB_REPO", "fg-tida-wg0/use-cases")

    to_add = sorted({new for _old, new in label_syncs if new})
    to_remove = sorted({old for old, new in label_syncs if old and old != new})

    if to_add:
        result = subprocess.run(
            ["gh", "issue", "edit", str(issue_number), "-R", repo, "--add-label", ",".join(to_add)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"warning: could not add label(s) {to_add}: {result.stderr.strip()}", file=sys.stderr)
        else:
            print(f"applied label(s) {to_add} to issue #{issue_number}")

    if to_remove:
        result = subprocess.run(
            ["gh", "issue", "edit", str(issue_number), "-R", repo, "--remove-label", ",".join(to_remove)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"warning: could not remove stale label(s) {to_remove}: {result.stderr.strip()}", file=sys.stderr)
        else:
            print(f"removed stale label(s) {to_remove} from issue #{issue_number} (role changed)")


def draft_wg_notifications(issue_number, entry, wgs_to_notify):
    """Print (never post) a notification comment for each newly-tagged WG.

    Only called for newly-added WG links, never on a re-run or a role
    change on an already-notified WG -- otherwise every tweak would print
    another draft for the same people.

    @-mentioning someone is a public statement attributed to this account,
    so this never posts automatically -- see docs/reviewer-workflow.md,
    "Notifying the tagged Working Group." Posting means actually running
    the `gh issue comment` command printed below, as its own deliberate
    step after a human reviews the wording."""
    if not wgs_to_notify:
        return
    load_env()
    repo = os.environ.get("GITHUB_REPO", "fg-tida-wg0/use-cases")

    mentions = sorted({f"@{contact}" for (_, _, contact) in wgs_to_notify if contact})
    if not mentions:
        print("warning: no github_contact on record for the newly-tagged WG(s) — no comment drafted", file=sys.stderr)
        return

    body_lines = [
        f"{' '.join(mentions)} — this use case has just been tagged to your Working Group "
        "in the WG0 use-case register.",
        "",
        f"**Summary:** {entry.get('summary') or '(no summary recorded)'}",
    ]
    if entry.get("review_notes"):
        body_lines += ["", f"**Notes:** {entry['review_notes']}"]
    body_lines += ["", f"**Working groups tagged:** {', '.join(entry.get('working_groups') or [])}"]
    body = "\n".join(body_lines)

    print(
        "\n--- Draft notification comment (NOT posted — review before sending) ---\n"
        f"{body}\n"
        "--- end draft ---\n"
        "To post it after review, run:\n"
        f'  gh issue comment {issue_number} -R {repo} --body-file <(cat <<\'EOF\'\n{body}\nEOF\n)'
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--issue", type=int, help="GitHub issue number to update")
    parser.add_argument("--status", choices=sorted(REVIEW_STATUSES))
    parser.add_argument("--summary")
    parser.add_argument("--notes")
    parser.add_argument("--priority")
    parser.add_argument("--reviewer")
    parser.add_argument("--add-theme", action="append", default=[], metavar="NAME")
    parser.add_argument(
        "--add-wg", action="append", default=[], metavar="SLUG[:primary|:secondary]",
        help="e.g. --add-wg wg1-cdra:primary; role is optional, and omitting it never erases a role set earlier",
    )
    parser.add_argument("--add-taxonomy-term", action="append", default=[], metavar="TERM")
    parser.add_argument("--add-standards-gap", action="append", default=[], metavar="TITLE")
    parser.add_argument("--related-to", type=int, metavar="ISSUE_NUMBER")
    parser.add_argument("--list-unmapped", action="store_true")
    parser.add_argument("--list-unreviewed", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(db_path())
    conn.execute("PRAGMA foreign_keys = ON")

    if args.list_unmapped:
        list_query(
            conn,
            "unmapped use cases",
            """
            SELECT uc.github_issue_number, uc.title FROM use_cases uc
            WHERE uc.id NOT IN (SELECT use_case_id FROM use_case_theme_links)
              AND uc.id NOT IN (SELECT use_case_id FROM use_case_working_group_links)
            ORDER BY uc.github_issue_number
            """,
        )
    if args.list_unreviewed:
        list_query(
            conn,
            "unreviewed use cases",
            """
            SELECT github_issue_number, title FROM use_cases
            WHERE review_status = 'new'
            ORDER BY github_issue_number
            """,
        )
    if args.list_unmapped or args.list_unreviewed:
        conn.close()
        return 0

    if args.issue is None:
        parser.error("--issue is required unless using --list-unmapped/--list-unreviewed")

    use_case_id = curation.get_use_case_id(conn, args.issue)
    if use_case_id is None:
        sys.exit(f"no use case found for issue #{args.issue}")

    updates, params = [], []
    for field, value in (
        ("review_status", args.status),
        ("summary", args.summary),
        ("review_notes", args.notes),
        ("priority", args.priority),
        ("reviewer", args.reviewer),
    ):
        if value is not None:
            updates.append(f"{field} = ?")
            params.append(value)
    if updates:
        if args.reviewer or args.status:
            updates.append("reviewed_at = datetime('now')")
        params.append(use_case_id)
        conn.execute(f"UPDATE use_cases SET {', '.join(updates)} WHERE id = ?", params)

    for theme in args.add_theme:
        curation.apply_theme_link(conn, use_case_id, theme)

    parsed_add_wg = []
    for raw in args.add_wg:
        wg_slug, sep, role = raw.partition(":")
        role = role or None
        if role not in (None, "primary", "secondary"):
            sys.exit(f"invalid role '{role}' in --add-wg {raw!r} — must be ':primary', ':secondary', or omitted")
        parsed_add_wg.append((wg_slug, role))

    new_primaries = [slug for slug, role in parsed_add_wg if role == "primary"]
    if len(new_primaries) > 1:
        sys.exit(f"at most one WG can be primary per use case — got multiple in this command: {new_primaries}")
    if new_primaries:
        existing_primary = curation.get_primary_wg_slug(conn, use_case_id)
        if existing_primary and existing_primary not in new_primaries:
            sys.exit(
                f"use case #{args.issue} already has primary WG '{existing_primary}' — "
                f"demote it explicitly (--add-wg {existing_primary}:secondary) before adding a new primary"
            )

    def labelled(github_label, role):
        if not github_label:
            return None
        return f"{github_label}:{role}" if role else github_label

    label_syncs = []    # (old_label_to_remove|None, new_label_to_add)
    wgs_to_notify = []  # subset of the above that also needs a fresh notification draft
    for wg_slug, role in parsed_add_wg:
        result = curation.apply_working_group_link(conn, use_case_id, wg_slug, role=role)
        if result is None:
            sys.exit(f"unknown working group slug '{wg_slug}' — add it to curation/reference-data.yaml first")

        old_label = labelled(result["github_label"], result["previous_role"])
        new_label = labelled(result["github_label"], result["role"])
        # is_new always needs a sync even if old_label == new_label (e.g.
        # tagged with no role both "before" and "after" -- there was no
        # real previous label on GitHub to compare against, since this is
        # the first time).
        if result["is_new"] or old_label != new_label:
            label_syncs.append((old_label if not result["is_new"] else None, new_label))
        if result["is_new"]:
            wgs_to_notify.append((wg_slug, new_label, result["github_contact"]))

    for term in args.add_taxonomy_term:
        curation.apply_taxonomy_link(conn, use_case_id, term)

    for gap_title in args.add_standards_gap:
        curation.apply_standards_gap_link(conn, use_case_id, gap_title)

    if args.related_to is not None:
        if not curation.apply_related_link(conn, use_case_id, args.related_to):
            sys.exit(f"no use case found for issue #{args.related_to}")

    conn.commit()

    mappings = curation.load_mappings()
    entry = read_current_entry(conn, args.issue)
    mappings[str(args.issue)] = entry
    curation.save_mappings(mappings)

    conn.close()
    print(f"updated issue #{args.issue} (curation/mappings.yaml written — remember to commit it)")

    sync_wg_labels(args.issue, label_syncs)
    draft_wg_notifications(args.issue, entry, wgs_to_notify)

    return 0


if __name__ == "__main__":
    sys.exit(main())
