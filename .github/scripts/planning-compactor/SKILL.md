---
name: planning-compactor
description: "Use this skill when a product's PLANNING.md has grown long and fully-completed milestones should be archived to PLANNING_ARCHIVE.md. Triggers on phrases like \"compact PLANNING\", \"archive completed milestones\", \"PLANNING is getting long\", \"trim the planning doc\", \"tidy up PLANNING\", \"clean up PLANNING\", or \"planning-compactor\". Always offer this skill proactively when reading a PLANNING.md over ~600 lines that has multiple fully-checked milestones — Seth has confirmed the file becomes painful to scroll past that size. Never overwrites without a dry-run preview."
---

# Planning Compactor Skill

Cuts fully-completed milestones out of `docs/PLANNING.md` and appends them to `docs/PLANNING_ARCHIVE.md`, keeping the working planning doc focused on what's actually in flight. Also re-renders the milestone overview table so it reflects only the milestones that remain.

**Spec source:** `STANDARDS.md §4.2` (PLANNING format) + `STANDARDS.md §9` "Milestone-close semantics" (the `## M<n>` H2 + flat top-level checkbox parsing contract this skill relies on).

**Why this skill exists.** `PLANNING.md` accumulates as a product matures. The implementer agent reads it on every step; humans scroll it whenever planning. Past ~600 lines, both readers suffer. The fix is mechanical: cut shipped milestones, keep them queryable in an archive file, leave the in-flight work in place. The skill exists because doing this by hand is tedious and easy to get wrong on a 700+ line file.

## Risk tier

`LOW` per `STANDARDS.md §9` — doc-only diff, no code paths affected, fully reversible by reverting the PR. The `commit` skill handles tier classification at commit time; the agent invoking this skill doesn't need to set it.

## Inputs

- **Product repo path.** The repo containing `docs/PLANNING.md`. Defaults to the current working directory when invoked from a product repo.
- **(Optional) `--apply` confirmation** when the agent transitions from dry-run preview to actually writing. Never apply without showing the dry-run first.

## Outputs

- **Updated `docs/PLANNING.md`** with archived milestone sections removed and the milestone overview table re-rendered.
- **Updated (or newly created) `docs/PLANNING_ARCHIVE.md`** with archived milestones prepended at the top **as paragraph-style summaries lifted from matching `CHANGELOG.md` entries** — not full milestone bodies. See "Archive shape" below.
- A structured JSON summary the agent presents to the user (in dry-run) or reports back after applying.

## Archive shape (v3 — 2026-05-25)

Each archived milestone renders as a compact summary block:

```markdown
### M3 — Decision Queue

**Shipped:** 2026-05-20 (PRs #11, #12)

Decision Queue at `/`. Cross-product agent escalations land here with resolve / snooze / pin / discuss actions; auto-expiry by risk tier; sibling `/follow-up` surface for non-blocking entries.
```

The paragraph under the **Shipped** line is a **composed summary** — the calling agent (whoever ran the skill) writes a one-paragraph description of what the milestone shipped, based on the matching `CHANGELOG.md` entry. The script does not synthesize content itself; its job is mechanical (extract CHANGELOG entry, present it, write the archive verbatim with the composed paragraph).

**Why this shape.** Archives are quick-reference, not future-task input. A reader six months later wants "what shipped here?" in one sentence, not a 13-bullet implementation detail dump. Composing per-milestone keeps that target — and forces a moment of curation at archive time rather than mechanical bulk-copy.

**No CHANGELOG match.** When no entry's title starts with the milestone ID (typical for early milestones that pre-date the title-with-milestone-ID convention), the archive renders a clear placeholder pointing at git history:

```markdown
### M1 — Foundation

*Archived 2026-05-25. No matching CHANGELOG entry found for `M1` — see git history of `docs/PLANNING.md` for the original milestone body.*
```

The calling agent MAY still provide a composed summary for unmatched milestones (e.g., reading from `PLANNING.md` body directly) — in that case the archive prefixes the paragraph with a provenance note (`*No CHANGELOG entry matched; summary composed from other sources.*`).

**Matching rule.** A CHANGELOG entry matches a milestone if its title **starts with** the milestone ID (`## 2026-05-20 — M3 Decision Queue` matches M3; `## 2026-05-23 — Fleet-doc drift sweep; §8 envelope honest rewrite; M5 scope expansion` does not match M5). Multiple matching entries aggregate (planning + implementation + complete → one body for the calling agent to read).

## Flow at a glance

Five steps. Step 3 is the new v3 composition step.

1. `--check` — verify §9 parsing contract
2. `--dry-run` — get the plan plus the raw CHANGELOG entry body per archivable milestone
3. **The calling agent composes a one-paragraph summary per milestone** from the CHANGELOG body, presents the proposed archive to the user, gets approval
4. Calling agent writes the composed summaries to a temp JSON file
5. `--apply --summaries=<json>` writes both files

## Flow

### Step 1 — Locate `PLANNING.md`

The skill operates on `docs/PLANNING.md` relative to the product repo root. If the file doesn't exist or isn't readable, stop and tell the user — the right next step is for them to confirm they're in the correct repo.

### Step 2 — Verify the parsing contract

Run `compact.py --check <path/to/PLANNING.md>`. The helper verifies the file follows the `STANDARDS.md §9` parsing contract:

- Milestone sections use H2 headings matching `## M<digits>` or `## M<digits> — <name>` or `## MFB.<digits>` (cross-milestone follow-ups).
- Each milestone contains `- [ ]` or `- [x]` checkboxes (either at H2 body level or under `### Steps` subsections).
- The milestone overview table (if present) appears once near the top and has a column whose cells contain the milestone IDs.

If the file fails the contract check, the helper reports which milestones don't match and exits non-zero. The agent surfaces this to the user — do not attempt to repair the file structure as part of compaction. Compaction is the wrong forum for that.

The skill is **strict about the §9 contract** intentionally. Products on legacy planning shapes (e.g., puzzle-pop's `## Phase N` format pre-migration) should either migrate their PLANNING.md to the §9 shape first or skip compaction until they do. Loosening the parser risks producing incorrect cuts on files we don't fully understand.

### Step 3 — Dry-run preview

Run `compact.py --dry-run <path/to/PLANNING.md>`. The helper auto-discovers `CHANGELOG.md` as a sibling of `PLANNING.md` and returns a structured plan including the raw CHANGELOG entry body per archivable milestone:

```json
{
  "mode": "dry-run",
  "planning_file": "docs/PLANNING.md",
  "archive_file": "docs/PLANNING_ARCHIVE.md",
  "changelog_file": "docs/CHANGELOG.md",
  "archive_exists": true,
  "archivable": [
    {
      "id": "M3", "title": "M3 — Decision Queue",
      "checkbox_count": 12, "line_count": 65,
      "changelog_summary": {
        "shipped_date": "2026-05-20",
        "pr_numbers": [11, 12],
        "matched_entry_titles": ["M3 Decision Queue"],
        "summary_bullets": ["...", "..."],   // legacy: kept for debugging
        "body_text": "### Entry from 2026-05-20 — M3 Decision Queue\n### For Users\n- ...\n### For Developers\n- ..."
      }
    }
  ],
  "non_archivable": [...],
  "header_areas_preserved": [...],
  "overview_table_changes": {...},
  "estimated_line_reduction": 312
}
```

The new field is **`body_text`** — the full raw markdown of the matched CHANGELOG entry (or aggregated bodies, if multiple entries matched). The calling agent reads this to compose the milestone's archive paragraph.

### Step 4 — Compose summaries

For each archivable milestone in the plan:

1. Read `changelog_summary.body_text`. If null (no CHANGELOG match), the calling agent MAY still compose a summary from the matching milestone body in `PLANNING.md` (read the section directly), or skip and let the script render a git-history placeholder.
2. **Compose ONE paragraph (1–3 sentences)** summarizing what shipped at a feature / outcome level — what a reader would want to know glancing at the archive months later. **Drop implementation detail** (Zod version, route registration gotchas, env secrets, dep bumps, migration filenames). Lean on the wording in the CHANGELOG entry's user-facing or headline bullets where they exist.
3. Avoid copy-pasting bullets verbatim — that's v2 behavior, deprecated. The whole point of v3 is that the agent does the curation.

Present the proposed archive (one paragraph per milestone) to the user. Ask for approval before applying. Edits welcome — the user may rewrite any paragraph that reads off.

### Step 5 — Apply

On user confirm, write the composed summaries to a temp JSON file keyed by milestone ID:

```json
{
  "M3": "Decision Queue at `/`. Cross-product agent escalations land here with resolve / snooze / pin / discuss actions; auto-expiry by risk tier; sibling `/follow-up` surface for non-blocking entries.",
  "M4": "Ideas Inbox at `/ideas`. Captures pre-product ideas with two-pane layout; status transitions (raw → under refinement → promoted → discarded) and timestamped triage notes. Replaces the fleet `inbox/` folder."
}
```

Then run:

```bash
python3 compact.py --apply --summaries=/tmp/summaries.json docs/PLANNING.md
```

The script:

1. Writes both files to temp paths first, then renames atomically — so a crash mid-write never leaves either file half-modified.
2. Renders each archived milestone using the composed paragraph from `--summaries`.
3. For milestones not in the summaries JSON: renders a placeholder (CHANGELOG-matched → "awaiting composed summary"; not matched → git-history pointer).
4. Cuts the archived milestones from `PLANNING.md` and re-renders the overview table.
3. Removes the corresponding rows from the milestone overview table in `PLANNING.md`. If the overview table becomes empty after removal, the table is left in place with the header row + divider so the structure is clear; the agent should mention this to the user.
4. Leaves header areas (`## Pre-implementation work in progress`, `## Out of scope`, `## Open questions`, `## Resolved`) and any in-flight milestones untouched.
5. Outputs the same structured JSON as dry-run, plus `"mode": "applied"` and a `wrote_files` list.

The helper is **idempotent**: re-running on a file with no fully-checked milestones is a clean no-op (exit 0, JSON reports zero archivable, zero changes).

### Step 5 — Report

Tell the user what was archived in one or two sentences, link to the archive file path, mention any caveats from the apply step (e.g., overview table now empty). Do not commit — that's the commit skill's job. The user will likely want to invoke `commit` next; mention it.

## Edge cases the helper handles

- **Archive file doesn't exist.** Created with a minimal header (`# PLANNING_ARCHIVE — <product-name>` + brief note about what lives in this file) followed by the new `## Archived YYYY-MM-DD` section.
- **Empty milestone (no checkboxes at all).** Not archivable. Can't tell "complete" from "never started" without a checkbox. Reported as non-archivable with reason "no checkboxes".
- **Mixed-status milestone.** Not archivable. All checkboxes under the H2 (recursively, across any `### Steps` or sub-sections) must be `- [x]`.
- **Cross-milestone follow-ups (MFB.x, security follow-ups).** Treated as milestones if their H2 matches `## MFB.<digits>` or contains a checklist with §9-compatible structure. Same archival rules apply. Helpers should keep narrative-only sections (no checkboxes) out of scope per the "no checkbox" rule above.
- **`---` section dividers.** Preserved adjacent to retained content; orphaned dividers (one before a cut milestone, one after the same milestone) are reduced to a single divider to avoid `--- --- ---` runs.
- **Milestone overview table missing.** Skill still works; just skips the table-rewrite step and reports it.
- **Multiple consecutive archivable milestones.** All cut in the same pass.

## What this skill does NOT do

- **Doesn't open a PR.** Just writes the two files. The user runs `commit` next.
- **Doesn't touch `LIFECYCLE.md`.** `next_milestone` reflects what's in flight, which is what's still in `PLANNING.md` after this skill runs. If the user wants `LIFECYCLE.md` updated, they can do that as part of the commit.
- **Doesn't try to fix planning files that don't follow §9.** Strict parser by design — compaction on a mis-structured file would produce bad results silently.
- **Doesn't archive partially-checked milestones.** Even if the user "knows" a milestone is shipped, we trust the checkboxes.
- **Doesn't reformat unchanged content.** Files in-place; bytes preserved for retained sections.
- **Doesn't synthesize summary content itself.** The script extracts the CHANGELOG entry and writes whatever the calling agent provides — it does not call an LLM or massage CHANGELOG bullets into a paragraph. That's the agent's job by design (see "Archive shape" above).

## Helper script

`compact.py` — Python 3 stdlib only (fleet convention per `security-review` aggregator).

Three modes (all take a path to `PLANNING.md` as positional arg):

- `--check` — verify §9 parsing contract; report which milestones don't match. Exit 0 if clean, non-zero with structured error otherwise. No file writes.
- `--dry-run` (default) — emit the structured plan JSON described in Step 3. No file writes.
- `--apply` — execute the plan and write the files. Same JSON output plus `wrote_files` list.

Invocation:

```bash
python3 skills/planning-compactor/compact.py --check docs/PLANNING.md
python3 skills/planning-compactor/compact.py --dry-run docs/PLANNING.md
python3 skills/planning-compactor/compact.py --apply docs/PLANNING.md
```

If running from a vended location (`Aethrix-Labs/.github:.github/scripts/planning-compactor/`), substitute the path. The skill is **not currently CI-wired** — it runs at human invocation; if it ever gets a CI consumer (e.g., scheduled compaction), it would need a `vending.json` per the §17.5 vending convention.

## When to use this skill — quick checklist

- A user asks to compact / archive / trim PLANNING.md (any product)
- You're about to read a PLANNING.md and notice it's >600 lines with multiple shipped milestones — **offer this skill proactively**, don't wait for the user to ask
- A milestone just closed in `commit` and PLANNING crossed the threshold as a result — same: offer it

## When NOT to use

- The user wants to delete in-flight milestones — not what this skill does; that's a manual edit
- The user wants to refactor PLANNING into a different shape — out of scope
- The product's PLANNING doesn't follow `STANDARDS.md §9` — fix the structure first, then come back to this skill
