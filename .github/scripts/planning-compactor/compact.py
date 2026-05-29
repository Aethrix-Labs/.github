#!/usr/bin/env python3
"""
compact.py — Archive fully-completed milestones from PLANNING.md to PLANNING_ARCHIVE.md.

Bundled with the `planning-compactor` skill. Python 3 stdlib only (fleet convention
per the security-review aggregator).

v3 behavior (2026-05-25): the archive carries a one-paragraph summary per milestone,
composed by the calling agent (not mechanically lifted from CHANGELOG bullets like v2).
compact.py extracts the raw CHANGELOG entry body and exposes it in dry-run JSON; the
calling agent reads that and composes a paragraph; --apply --summaries=<json> writes
the archive using the composed paragraphs. Rationale: full milestone bodies aren't
load-bearing six months after ship, and even mechanical bullet-lifting (v2) was too
verbose for an archive's purpose. Falls back to clear placeholders when no CHANGELOG
match exists (git-history pointer) or no composed summary provided (visible placeholder).

Three modes:
  --check    Verify the file follows STANDARDS.md §9 parsing contract. Exit 0 if clean.
  --dry-run  (default) Emit the archive plan as JSON, including the summary that would
             be written for each archivable milestone. No file writes.
  --apply    Execute the plan: write PLANNING.md (with milestones cut and overview table
             re-rendered) + PLANNING_ARCHIVE.md (with archived sections prepended in
             summary form). Atomic writes via temp-file-then-rename so a crash mid-write
             can't leave either file half-modified.

Invocation:
  python3 compact.py --check    docs/PLANNING.md
  python3 compact.py --dry-run  docs/PLANNING.md
  python3 compact.py --apply    docs/PLANNING.md

Exit codes:
  0  Clean run (any mode)
  1  Parsing-contract failure (--check) or write failure (--apply)
  2  Bad invocation (missing path, bad flags, file not found)

Output:
  JSON to stdout describing the plan / what was done.

Reference docs the parser relies on:
  - STANDARDS.md §4.2 — PLANNING.md format
  - STANDARDS.md §9   — `## M<n>` H2-milestone / flat-checkbox-step parsing contract
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ─── Heading classification ────────────────────────────────────────────────────

# H2 headings whose section is NEVER archived (header areas, end-matter, the
# overview table itself). Match is case-insensitive substring on the heading text
# (after the leading `## `).
PRESERVED_HEADING_SUBSTRINGS = (
    "pre-implementation work in progress",
    "milestone overview",
    "out of scope",
    "open questions",
)

# Pattern for a milestone-shaped H2 heading per STANDARDS.md §9. Matches the
# common shapes we see in the fleet today: `## M5`, `## M5 — name`,
# `## MFB.1 — name`. Cross-milestone follow-ups whose H2 doesn't match this
# pattern are still eligible if they contain checkboxes — see classify_section.
MILESTONE_HEADING_RE = re.compile(
    r"^##\s+(M\d+|MFB\.\d+)\b",
    re.IGNORECASE,
)

# Patterns for the milestone overview table — a markdown table whose first
# non-header data row starts with `| M<digits>` or similar. Found inside the
# `## Milestone overview` H2 section.
TABLE_DIVIDER_RE = re.compile(r"^\|\s*-{3,}\s*(\|\s*-{3,}\s*)+\|?\s*$")
TABLE_ROW_RE = re.compile(r"^\|.*\|\s*$")
OVERVIEW_ROW_ID_RE = re.compile(
    r"\|\s*\*?\*?(M\d+|MFB\.\d+)\b",
    re.IGNORECASE,
)

# Checkbox detection (either at top-level or under sub-headings).
OPEN_CHECKBOX_RE = re.compile(r"^\s*-\s+\[\s\]")
CLOSED_CHECKBOX_RE = re.compile(r"^\s*-\s+\[x\]", re.IGNORECASE)


@dataclass
class Section:
    """One H2-anchored section of PLANNING.md."""

    heading_line: str           # e.g., "## M1 — Foundation"
    heading_text: str           # e.g., "M1 — Foundation"
    body_lines: list[str] = field(default_factory=list)  # everything after the heading line, up to next H2 (heading line itself NOT included)
    start_line: int = 0         # 0-indexed line number of the heading in the source
    end_line: int = 0           # 0-indexed line number AFTER the last line of this section
    classification: str = ""    # "preserved" | "milestone" | "non-milestone" | "overview-table"
    open_checkboxes: int = 0
    closed_checkboxes: int = 0
    milestone_id: Optional[str] = None  # e.g., "M1", "MFB.1" — None for non-milestone sections

    @property
    def total_checkboxes(self) -> int:
        return self.open_checkboxes + self.closed_checkboxes

    @property
    def fully_closed(self) -> bool:
        return self.total_checkboxes > 0 and self.open_checkboxes == 0

    @property
    def line_count(self) -> int:
        # +1 for the heading line itself
        return 1 + len(self.body_lines)

    def raw_text(self) -> str:
        """Reproduce the section's source text exactly."""
        return self.heading_line + "".join(self.body_lines)


def parse_sections(text: str) -> tuple[str, list[Section]]:
    """
    Split PLANNING.md text into (prelude, sections).

    Prelude is everything before the first H2 (the file's H1, intro text, etc.).
    Each section starts at an H2 heading and runs up to (but not including) the
    next H2 or EOF.
    """
    lines = text.splitlines(keepends=True)
    sections: list[Section] = []
    prelude_lines: list[str] = []
    current: Optional[Section] = None

    for idx, line in enumerate(lines):
        if line.startswith("## ") and not line.startswith("### "):
            # Close out the previous section if any
            if current is not None:
                current.end_line = idx
                sections.append(current)
            # Start a new section
            heading_line = line
            heading_text = line.lstrip("# ").rstrip("\n").rstrip()
            current = Section(
                heading_line=heading_line,
                heading_text=heading_text,
                start_line=idx,
            )
        elif current is None:
            prelude_lines.append(line)
        else:
            current.body_lines.append(line)

    if current is not None:
        current.end_line = len(lines)
        sections.append(current)

    return "".join(prelude_lines), sections


def is_preserved(heading_text: str) -> bool:
    h = heading_text.lower()
    return any(sub in h for sub in PRESERVED_HEADING_SUBSTRINGS)


def looks_like_overview_table_section(heading_text: str) -> bool:
    return "milestone overview" in heading_text.lower()


def count_checkboxes(body_lines: list[str]) -> tuple[int, int]:
    open_count = 0
    closed_count = 0
    for line in body_lines:
        if OPEN_CHECKBOX_RE.match(line):
            open_count += 1
        elif CLOSED_CHECKBOX_RE.match(line):
            closed_count += 1
    return open_count, closed_count


def extract_milestone_id(heading_text: str) -> Optional[str]:
    """Pull the milestone ID out of a heading like 'M5 — Agent Activity Trail'."""
    # Strip leading markdown emphasis like '## **READY** — M5 …' just in case.
    cleaned = heading_text.lstrip("*").strip()
    m = re.match(r"^(M\d+|MFB\.\d+)\b", cleaned, re.IGNORECASE)
    return m.group(1).upper() if m else None


def classify_sections(sections: list[Section]) -> None:
    """Mutate sections in place with classification + checkbox counts."""
    for s in sections:
        s.open_checkboxes, s.closed_checkboxes = count_checkboxes(s.body_lines)

        if looks_like_overview_table_section(s.heading_text):
            s.classification = "overview-table"
            continue

        if is_preserved(s.heading_text):
            s.classification = "preserved"
            continue

        s.milestone_id = extract_milestone_id(s.heading_text)

        # A section is milestone-shaped if EITHER:
        #   (a) its heading matches the M<n> / MFB.<n> pattern, OR
        #   (b) it has checkboxes and isn't in any preserved bucket.
        # The intent is to catch cross-milestone follow-up sections whose H2 doesn't
        # match the strict ID pattern but which are semantically planning work.
        if s.milestone_id is not None or s.total_checkboxes > 0:
            s.classification = "milestone"
        else:
            s.classification = "non-milestone"


# ─── Parsing-contract check ────────────────────────────────────────────────────


def check_contract(sections: list[Section]) -> tuple[list[str], list[str]]:
    """
    Verify the parsed sections satisfy STANDARDS.md §9 expectations.

    Returns (errors, notes):
      - errors  — block compaction; check exits non-zero if any present.
      - notes   — informational only; surfaced but don't fail the check.

    The distinction matters because legitimate stubs (e.g. M11 deferred-stub
    milestones) shouldn't block the user from running the skill on a file
    that's otherwise contract-clean.
    """
    errors: list[str] = []
    notes: list[str] = []

    overview_count = sum(1 for s in sections if s.classification == "overview-table")
    if overview_count > 1:
        errors.append(
            f"expected at most one 'Milestone overview' H2 section; found {overview_count}"
        )

    milestone_sections = [s for s in sections if s.classification == "milestone"]
    for s in milestone_sections:
        # Milestone-pattern heading but zero checkboxes is usually a legitimate
        # stub. We surface it as a note (so the agent can mention it to the user)
        # but don't fail the check — the section will simply not be archivable.
        if s.milestone_id and s.total_checkboxes == 0:
            notes.append(
                f"milestone '{s.milestone_id}' has zero checkboxes — likely a stub. "
                "Not archivable (compactor needs at least one checkbox to consider a "
                "section 'complete')."
            )

    # Duplicate milestone IDs WOULD break the overview-table-row removal logic, so
    # this is a real error.
    seen: dict[str, int] = {}
    for s in milestone_sections:
        if s.milestone_id:
            seen[s.milestone_id] = seen.get(s.milestone_id, 0) + 1
    for mid, count in seen.items():
        if count > 1:
            errors.append(
                f"milestone ID '{mid}' appears in {count} H2 sections — duplicate IDs"
                " break overview-table row removal. Resolve before compacting."
            )

    return errors, notes


# ─── Overview table re-rendering ───────────────────────────────────────────────


def rewrite_overview_table(body_lines: list[str], archived_ids: set[str]) -> tuple[list[str], list[str]]:
    """
    Remove rows whose first ID-cell matches an archived milestone ID from any
    markdown tables in the section body. Returns (new_body_lines, removed_row_ids).
    """
    new_lines: list[str] = []
    removed: list[str] = []
    in_table = False
    table_header_seen = False

    for line in body_lines:
        if TABLE_ROW_RE.match(line):
            if not in_table:
                in_table = True
                table_header_seen = False
            if TABLE_DIVIDER_RE.match(line):
                table_header_seen = True
                new_lines.append(line)
                continue
            if not table_header_seen:
                # Header row — keep
                new_lines.append(line)
                continue
            # Data row — check if it should be removed
            m = OVERVIEW_ROW_ID_RE.match(line)
            if m and m.group(1).upper() in archived_ids:
                removed.append(m.group(1).upper())
                continue
            new_lines.append(line)
        else:
            if in_table:
                in_table = False
            new_lines.append(line)

    return new_lines, removed


# ─── Plan construction ────────────────────────────────────────────────────────


def build_plan(
    planning_path: Path,
    sections: list[Section],
    archive_exists: bool,
    changelog_text: Optional[str] = None,
) -> dict:
    archivable = []
    non_archivable = []
    preserved_names = []

    for s in sections:
        if s.classification == "preserved":
            preserved_names.append(s.heading_text)
            continue
        if s.classification == "overview-table":
            preserved_names.append(s.heading_text)
            continue
        if s.classification != "milestone":
            continue
        if s.fully_closed:
            summary = None
            if changelog_text and s.milestone_id:
                summary = extract_changelog_summary(changelog_text, s.milestone_id)
            archivable.append({
                "id": s.milestone_id,
                "title": s.heading_text,
                "checkbox_count": s.total_checkboxes,
                "line_count": s.line_count,
                "changelog_summary": summary,  # None if no match, dict otherwise
            })
        else:
            reason = (
                "no checkboxes" if s.total_checkboxes == 0
                else f"{s.open_checkboxes} open checkbox" + ("es" if s.open_checkboxes != 1 else "")
            )
            non_archivable.append({
                "id": s.milestone_id,
                "title": s.heading_text,
                "checkbox_count": s.total_checkboxes,
                "open_count": s.open_checkboxes,
                "reason": reason,
            })

    archived_ids = {a["id"] for a in archivable if a["id"]}

    overview_changes = {"rows_to_remove": [], "rows_to_keep": []}
    for s in sections:
        if s.classification != "overview-table":
            continue
        # Pre-compute what would be removed and what kept (the actual rewrite happens at apply time).
        for line in s.body_lines:
            m = OVERVIEW_ROW_ID_RE.match(line)
            if m:
                mid = m.group(1).upper()
                if mid in archived_ids:
                    overview_changes["rows_to_remove"].append(mid)
                else:
                    overview_changes["rows_to_keep"].append(mid)

    archive_path = planning_path.with_name("PLANNING_ARCHIVE.md")
    estimated_line_reduction = sum(a["line_count"] for a in archivable)

    return {
        "planning_file": str(planning_path),
        "archive_file": str(archive_path),
        "archive_exists": archive_exists,
        "archivable": archivable,
        "non_archivable": non_archivable,
        "header_areas_preserved": preserved_names,
        "overview_table_changes": overview_changes,
        "estimated_line_reduction": estimated_line_reduction,
    }




# ─── CHANGELOG summary extraction (v2) ─────────────────────────────────────────

# A CHANGELOG entry starts with `## YYYY-MM-DD — <title>`. Matching milestone IDs
# inside that title (or its body) means the entry describes work on that milestone.
CHANGELOG_HEADING_RE = re.compile(
    r"^##\s+(\d{4}-\d{2}-\d{2})\s+[\u2014\-]\s+(.+?)$",
    re.MULTILINE,
)
PR_REF_RE = re.compile(r"#(\d+)\b")


def find_changelog_path(planning_path: Path) -> Optional[Path]:
    """Look for CHANGELOG.md as a sibling of PLANNING.md."""
    candidate = planning_path.with_name("CHANGELOG.md")
    return candidate if candidate.exists() else None


def _split_changelog_entries(changelog_text: str) -> list[tuple[str, str, str]]:
    """
    Return list of (date_str, title, body) tuples — one per ## entry, in source order.

    body is everything from the heading line down to (but not including) the next
    ## heading or EOF. Includes the heading itself stripped from the front for
    simpler downstream processing.
    """
    entries: list[tuple[str, str, str]] = []
    matches = list(CHANGELOG_HEADING_RE.finditer(changelog_text))
    for i, m in enumerate(matches):
        date_str = m.group(1)
        title = m.group(2).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(changelog_text)
        body = changelog_text[body_start:body_end]
        entries.append((date_str, title, body))
    return entries


def _milestone_match_regex(milestone_id: str) -> re.Pattern:
    """Build a regex that matches the milestone ID at the START of a title.

    Anchored at start to avoid spurious matches from titles like
    "Fleet-doc drift sweep; §8 envelope honest rewrite; M5 scope expansion"
    where the milestone ID is incidental rather than the entry's primary topic.
    Allows trailing punctuation (`:`, space) so titles like "M8: Hub MCP" and
    "M4 Ideas Inbox" both match. `re.escape` handles MFB.<N>'s literal dot.
    """
    pattern = r"^" + re.escape(milestone_id) + r"\b"
    return re.compile(pattern, re.IGNORECASE)


def extract_changelog_summary(
    changelog_text: str,
    milestone_id: str,
) -> Optional[dict]:
    """
    Find all CHANGELOG entries that mention milestone_id (in title or body),
    aggregate their bullets and PR refs, return a summary dict.

    Returns None if no entry matches.

    Returned dict:
      {
        "shipped_date": "YYYY-MM-DD",        # latest matching entry's date
        "pr_numbers": [11, 12],                # union of PR refs across matches
        "summary_bullets": ["bullet 1", ...],  # top-level "- " bullets from matched bodies
        "matched_entry_titles": ["title1", ...],
      }
    """
    if not milestone_id:
        return None
    mid_re = _milestone_match_regex(milestone_id)
    matched = []
    for date_str, title, body in _split_changelog_entries(changelog_text):
        # Match ONLY in the title — cross-references in body text (e.g., grooming
        # passes that mention "Agent Activity Trail (M5 ship 2026-05-23)") would
        # otherwise produce spurious matches and inflate the PR list with PRs
        # that didn't ship the milestone. Going forward, the `commit` skill puts
        # the milestone ID in the CHANGELOG title for milestone-shipping entries,
        # so title-match is the right precision target.
        if mid_re.search(title):
            matched.append((date_str, title, body))
    if not matched:
        return None

    # Aggregate
    dates = sorted(date for date, _, _ in matched)
    shipped_date = dates[-1]  # latest

    pr_numbers: list[int] = []
    seen_prs: set[int] = set()
    summary_bullets: list[str] = []

    for _, _, body in matched:
        # Collect PR references
        for m in PR_REF_RE.finditer(body):
            n = int(m.group(1))
            if n not in seen_prs:
                seen_prs.add(n)
                pr_numbers.append(n)
        # Collect top-level bullets only (not indented sub-bullets) — preserve the
        # natural curation of "headline points" from the CHANGELOG author.
        for line in body.splitlines():
            stripped = line.rstrip()
            if stripped.startswith("- ") and not stripped.startswith("  -"):
                summary_bullets.append(stripped[2:].strip())

    # Aggregate the raw bodies of all matched entries — calling agent uses this to
    # compose the one-paragraph summary in v3. Newline-separated with a small header
    # per matched entry so multi-entry milestones (M7-planning + M7-impl + M7-complete)
    # stay readable.
    body_text_parts = []
    for date_str, title, body in matched:
        body_text_parts.append(f"### Entry from {date_str} — {title}\n{body.strip()}\n")
    body_text = "\n".join(body_text_parts).strip()

    return {
        "shipped_date": shipped_date,
        "pr_numbers": sorted(pr_numbers),
        "summary_bullets": summary_bullets,  # kept for debugging / backward inspection
        "body_text": body_text,                # NEW v3: raw entry text for composed summarization
        "matched_entry_titles": [title for _, title, _ in matched],
    }


def render_milestone_summary_block(
    archived_section: "Section",
    changelog_summary: Optional[dict],
    composed_summary: Optional[str],
    archive_date: str,
) -> str:
    """
    Render one archived milestone as a summary block (v3 shape).

    v3 contract: the calling agent composes a one-paragraph summary per milestone
    by reading the CHANGELOG entry body (returned in dry-run JSON) and passes the
    paragraph back via --apply --summaries=<json>. This function uses the composed
    paragraph as the milestone's archive content.

    Shape:
        ### <heading_text>

        **Shipped:** <date> (PRs #N, #M)

        <composed paragraph from calling agent>

    Fallback paths:
      - No CHANGELOG match AND no composed summary → placeholder pointing at git
      - CHANGELOG match exists but no composed summary → "Awaiting composed summary"
        placeholder. This usually means the caller invoked --apply without
        providing the milestone's summary in --summaries; treat as a visible bug.
    """
    heading = archived_section.heading_text  # e.g., "M3 — Decision Queue"
    parts = [f"### {heading}\n\n"]

    # Case A: no CHANGELOG match → git-history placeholder regardless of composed.
    if changelog_summary is None:
        if composed_summary:
            # Caller provided one anyway (e.g., from PLANNING content or domain knowledge);
            # honor it but tag the provenance.
            parts.append(f"*Archived {archive_date}. No CHANGELOG entry matched; summary composed from other sources.*\n\n")
            parts.append(composed_summary.strip() + "\n\n")
        else:
            parts.append(
                f"*Archived {archive_date}. No matching CHANGELOG entry found for "
                f"`{archived_section.milestone_id or heading}` — see git history of "
                "`docs/PLANNING.md` for the original milestone body.*\n\n"
            )
        return "".join(parts)

    # Case B + C: CHANGELOG matched.
    shipped = changelog_summary["shipped_date"]
    prs = changelog_summary["pr_numbers"]
    if prs:
        pr_str = ", ".join(f"#{n}" for n in prs)
        parts.append(f"**Shipped:** {shipped} (PRs {pr_str})\n\n")
    else:
        parts.append(f"**Shipped:** {shipped}\n\n")

    if composed_summary and composed_summary.strip():
        parts.append(composed_summary.strip() + "\n\n")
    else:
        parts.append(
            f"*CHANGELOG entry matched but no composed summary was provided. "
            "Re-run --apply with --summaries to fill this in, or see "
            f"`docs/CHANGELOG.md` entry: {changelog_summary['matched_entry_titles'][0]}.*\n\n"
        )

    return "".join(parts)


# ─── Apply ─────────────────────────────────────────────────────────────────────


def render_new_planning(
    prelude: str,
    sections: list[Section],
    archived_ids: set[str],
) -> str:
    parts: list[str] = [prelude]

    for s in sections:
        if s.classification == "milestone" and s.milestone_id in archived_ids:
            # Cut: do not include this section.
            continue
        if s.classification == "overview-table":
            new_body, _ = rewrite_overview_table(s.body_lines, archived_ids)
            parts.append(s.heading_line + "".join(new_body))
        else:
            parts.append(s.raw_text())

    text = "".join(parts)

    # Collapse runs of consecutive `---` divider lines (with only whitespace between)
    # down to a single divider. Common after cutting a section bracketed by dividers.
    text = re.sub(
        r"(\n---\s*\n\s*){2,}",
        "\n---\n\n",
        text,
    )

    return text


def render_archive_prepend(
    archived_sections: list[Section],
    archive_date: str,
    changelog_summaries: dict[str, Optional[dict]],
    composed_summaries: dict[str, str],
) -> str:
    """Build the new content block to prepend to PLANNING_ARCHIVE.md.

    v3: each archived milestone renders as a summary block (heading + shipped
    date + PR list + composed paragraph). `changelog_summaries` carries the
    CHANGELOG-extracted metadata per milestone (matched entry titles, dates,
    PR refs, raw body); `composed_summaries` carries the one-paragraph string
    the calling agent composed per milestone. Both keyed by milestone_id (or
    heading text for unkeyed milestones).
    """
    parts = [f"## Archived {archive_date}\n\n"]
    for s in archived_sections:
        key = s.milestone_id or s.heading_text
        changelog_summary = changelog_summaries.get(key)
        composed_summary = composed_summaries.get(key)
        parts.append(
            render_milestone_summary_block(s, changelog_summary, composed_summary, archive_date)
        )
    parts.append("---\n\n")
    return "".join(parts)


def archive_file_initial_content(product_repo_name: str) -> str:
    return (
        f"# PLANNING_ARCHIVE — {product_repo_name}\n\n"
        "Archived milestones from `PLANNING.md`, in reverse-chronological order by\n"
        "archive date (most recent at the top). New archives are prepended here by the\n"
        "`planning-compactor` skill when their checkboxes are all closed in `PLANNING.md`.\n\n"
        "Each archived milestone is rendered as a one-paragraph summary composed by\n"
        "the agent that ran the compactor, based on the matching `CHANGELOG.md` entry.\n"
        "Full milestone bodies (acceptance criteria, sub-step notes, etc.) are intentionally\n"
        "not preserved here; consult git history of `PLANNING.md` if reconstruction is\n"
        "ever needed.\n\n"
        "---\n\n"
    )


def atomic_write(path: Path, content: str) -> None:
    """Write content to a temp file in the same directory, then rename atomically."""
    fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def apply_plan(
    planning_path: Path,
    sections: list[Section],
    plan: dict,
    archive_date: str,
    composed_summaries: Optional[dict[str, str]] = None,
) -> dict:
    """Execute the plan: write both files atomically. Returns the result dict."""
    archived_ids = {a["id"] for a in plan["archivable"] if a["id"]}
    archived_sections = [
        s for s in sections
        if s.classification == "milestone" and s.milestone_id in archived_ids
    ]

    # CHANGELOG summaries from build_plan, keyed by milestone_id.
    changelog_summaries: dict[str, Optional[dict]] = {}
    for a in plan["archivable"]:
        key = a.get("id") or a.get("title")
        if key:
            changelog_summaries[key] = a.get("changelog_summary")

    composed_summaries = composed_summaries or {}

    # Read prelude separately so render_new_planning can prepend it back in.
    with planning_path.open("r", encoding="utf-8") as f:
        full_text = f.read()
    prelude, _ = parse_sections(full_text)

    new_planning_text = render_new_planning(prelude, sections, archived_ids)

    archive_path = Path(plan["archive_file"])
    if archive_path.exists():
        existing = archive_path.read_text(encoding="utf-8")
        h2_match = re.search(r"^## ", existing, re.MULTILINE)
        if h2_match:
            insert_at = h2_match.start()
            prepend = render_archive_prepend(archived_sections, archive_date, changelog_summaries, composed_summaries)
            new_archive_text = existing[:insert_at] + prepend + existing[insert_at:]
        else:
            new_archive_text = existing + "\n" + render_archive_prepend(archived_sections, archive_date, changelog_summaries, composed_summaries)
    else:
        product_name = planning_path.parent.parent.name or "product"
        new_archive_text = (
            archive_file_initial_content(product_name)
            + render_archive_prepend(archived_sections, archive_date, changelog_summaries, composed_summaries)
        )

    wrote: list[str] = []
    if archived_sections:
        atomic_write(planning_path, new_planning_text)
        wrote.append(str(planning_path))
        atomic_write(archive_path, new_archive_text)
        wrote.append(str(archive_path))

    result = dict(plan)
    result["mode"] = "applied"
    result["wrote_files"] = wrote
    result["archive_date"] = archive_date
    return result


# ─── CLI ──────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive completed milestones from PLANNING.md")
    parser.add_argument("planning_path", help="Path to docs/PLANNING.md")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Verify parsing contract; no writes")
    mode.add_argument("--dry-run", action="store_true", help="Emit plan as JSON; no writes (default)")
    mode.add_argument("--apply", action="store_true", help="Execute the plan; write files atomically")
    parser.add_argument(
        "--archive-date",
        help="Override archive-date stamp (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--summaries",
        help="Path to a JSON file mapping milestone_id → composed-summary paragraph. "
             "Used by --apply to render the archive entries. If omitted, milestones "
             "with a CHANGELOG match render an \"awaiting composed summary\" placeholder; "
             "milestones without a match render a git-history pointer.",
    )
    args = parser.parse_args()

    planning_path = Path(args.planning_path)
    if not planning_path.exists():
        print(json.dumps({"error": f"PLANNING file not found: {planning_path}"}), file=sys.stderr)
        return 2
    if not planning_path.is_file():
        print(json.dumps({"error": f"Not a file: {planning_path}"}), file=sys.stderr)
        return 2

    text = planning_path.read_text(encoding="utf-8")
    prelude, sections = parse_sections(text)
    classify_sections(sections)

    if args.check:
        errors, notes = check_contract(sections)
        out = {
            "mode": "check",
            "planning_file": str(planning_path),
            "section_count": len(sections),
            "classifications": {
                cls: sum(1 for s in sections if s.classification == cls)
                for cls in ("preserved", "overview-table", "milestone", "non-milestone")
            },
            "errors": errors,
            "notes": notes,
        }
        print(json.dumps(out, indent=2))
        return 1 if errors else 0

    archive_path = planning_path.with_name("PLANNING_ARCHIVE.md")
    changelog_path = find_changelog_path(planning_path)
    changelog_text = changelog_path.read_text(encoding="utf-8") if changelog_path else None
    plan = build_plan(
        planning_path,
        sections,
        archive_exists=archive_path.exists(),
        changelog_text=changelog_text,
    )
    plan["changelog_file"] = str(changelog_path) if changelog_path else None

    if args.apply:
        archive_date = args.archive_date or datetime.date.today().isoformat()
        composed_summaries: Optional[dict[str, str]] = None
        if args.summaries:
            summaries_path = Path(args.summaries)
            if not summaries_path.exists():
                print(json.dumps({"error": f"--summaries file not found: {summaries_path}"}), file=sys.stderr)
                return 2
            try:
                composed_summaries = json.loads(summaries_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                print(json.dumps({"error": f"--summaries JSON parse failed: {e}"}), file=sys.stderr)
                return 2
            if not isinstance(composed_summaries, dict):
                print(json.dumps({"error": "--summaries JSON must be an object mapping milestone_id → string"}), file=sys.stderr)
                return 2
        result = apply_plan(planning_path, sections, plan, archive_date, composed_summaries)
        print(json.dumps(result, indent=2))
        return 0

    # Default: dry-run
    plan["mode"] = "dry-run"
    print(json.dumps(plan, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
