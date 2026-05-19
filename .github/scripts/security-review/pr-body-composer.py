#!/usr/bin/env python3
"""
pr-body-composer.py — security-review PR body section composer.

Reads `deterministic-findings.json` (from aggregator.py) and `semantic-findings.json`
(from the Claude semantic step) and writes a unified `## Security review`
section to the PR body via the GitHub REST API.

Source of truth: /skills/security-review/pr-body-composer.py (sethgibson-com)
Vended to:       .github/scripts/security-review/pr-body-composer.py in each product repo
Spec:            /docs/SECURITY_REVIEW_REDESIGN.md §8 (PR body composition)
Workflow ref:    /skills/security-review/workflow-template.yml (semantic job)

Behavior per §8
---------------
- All findings sorted by severity (Critical → Important → Advisory).
- Each finding tags its source tool: Gitleaks / Semgrep / npm audit / Claude semantic / Claude triage.
- Tools line shows ✓ for each layer that ran (presence of its findings file).
- Summary line: "{C} Critical, {I} Important, {A} Advisory."
- If both passes ran AND total findings == 0: section is OMITTED (deleted if present).
- If semantic was skipped per §3.3: section includes only the skip notice + deterministic state.

Sentinels
---------
The section is bounded by HTML comment markers so the composer can find/replace
on subsequent runs (push events on the same PR):

    <!-- security-review:start -->
    ## Security review
    ...
    <!-- security-review:end -->

If the markers are absent from the PR body, the section is appended at the end.
If present, the bounded region is replaced.

Auth
----
Uses `GITHUB_TOKEN` (env or --github-token arg). The workflow auto-provides
`secrets.GITHUB_TOKEN`. Required workflow permission: `pull-requests: write`.

Stdlib only. Compatible with Python 3.9+.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SECTION_START = "<!-- security-review:start -->"
SECTION_END = "<!-- security-review:end -->"

# Map of internal `tool` values → human-readable labels for the source-tool tag.
TOOL_LABELS = {
    "gitleaks": "Gitleaks",
    "semgrep": "Semgrep",
    "dep-audit": "npm audit",
    "semantic-auth": "Claude semantic",
    "semantic-idor": "Claude semantic",
    "semantic-data-rest": "Claude semantic",
    "triage": "Claude triage",
}

# Map of severity → sort weight + emoji + label.
SEVERITY_META = {
    "critical": (0, "🔴", "Critical"),
    "important": (1, "🟡", "Important"),
    "advisory": (2, "🔵", "Advisory"),
}


def _load_json(path: Path | None) -> dict | None:
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"::warning::could not parse {path}: {e}", file=sys.stderr)
        return None


def _finding_location(f: dict) -> str:
    """Format `file:line` or `file` or '' depending on what's present."""
    file = f.get("file")
    line = f.get("line_start")
    if file and line:
        return f"`{file}:{line}`"
    if file:
        return f"`{file}`"
    return ""


def _finding_block(f: dict) -> str:
    """Render a single finding as a markdown list item with risk/fix sub-bullets."""
    tool_label = TOOL_LABELS.get(f.get("tool", ""), f.get("tool", "?"))
    title = f.get("title", "(untitled)")
    loc = _finding_location(f)

    # Header line — title + location + source tool tag
    header = f"**{title}**"
    if loc:
        header += f" ({loc})"
    header += f" — *via {tool_label}*"

    # Description gets split into Risk / Suggested fix where possible.
    # The SKILL.md description format is "what + suggested fix". For deterministic
    # findings the description is usually just a CVE URL or short label. We render
    # whatever is present as a single sub-bullet to avoid over-engineering parsing.
    desc = (f.get("description") or "").strip()
    if desc:
        # Indent multi-line descriptions
        desc_indented = "\n     ".join(desc.splitlines())
        body = f"   - {desc_indented}"
    else:
        body = "   - (no description)"

    # Package context for dep-audit findings
    pkg = f.get("package")
    if pkg:
        body = f"   - Package: `{pkg}`\n{body}"

    return f"1. {header}\n{body}"


def _findings_by_severity(findings: list[dict]) -> dict[str, list[dict]]:
    buckets: dict[str, list[dict]] = {"critical": [], "important": [], "advisory": []}
    for f in findings:
        sev = (f.get("severity") or "advisory").lower()
        if sev not in buckets:
            sev = "advisory"
        buckets[sev].append(f)
    return buckets


def compose_section(det: dict | None, sem: dict | None,
                    semantic_skipped: bool, skip_reason: str | None) -> str | None:
    """Build the markdown section. Returns None if section should be omitted (clean state)."""
    det_findings: list[dict] = (det or {}).get("findings", []) or []
    sem_findings: list[dict] = (sem or {}).get("findings", []) or []
    all_findings = det_findings + sem_findings

    det_ran = det is not None
    sem_ran = sem is not None  # not the same as semantic_skipped; sem can be None even when not skipped (if semantic failed catastrophically)

    # Spec §8 rules in order:
    #   1. "If Claude was skipped per §3.3: include only the skip notice."
    #      → falls through to render (skip notice is part of the body builder).
    #   2. "If no findings and Claude ran: omit the section."
    #      → return None here.
    # Skipped runs are NEVER treated as clean for the omit rule, even if
    # everything else is empty. The user always wants to see the skip notice.
    if det_ran and sem_ran and len(all_findings) == 0 and not semantic_skipped:
        return None

    buckets = _findings_by_severity(all_findings)
    counts = {sev: len(buckets[sev]) for sev in ("critical", "important", "advisory")}

    # --- Tools line --------------------------------------------------------
    tools_parts = []
    if det_ran:
        tools_parts.extend(["Gitleaks ✓", "Semgrep ✓", "npm audit ✓"])
    if sem_ran:
        tools_parts.append("Claude semantic ✓")
    elif semantic_skipped:
        tools_parts.append("Claude semantic (skipped)")
    else:
        tools_parts.append("Claude semantic (not run)")
    tools_line = ", ".join(tools_parts)

    # --- Summary line ------------------------------------------------------
    summary_line = f"{counts['critical']} Critical, {counts['important']} Important, {counts['advisory']} Advisory."

    # --- Body --------------------------------------------------------------
    lines: list[str] = [
        SECTION_START,
        "## Security review",
        "",
        f"**Tools:** {tools_line}",
        f"**Summary:** {summary_line}",
        "",
    ]

    # Skip notice if applicable
    if semantic_skipped:
        reason = skip_reason or "(no reason recorded)"
        lines.extend([
            "_Claude semantic review skipped per `SECURITY_REVIEW_REDESIGN.md §3.3`:_",
            f"```",
            reason,
            f"```",
            "",
        ])

    # Per-severity sections
    for sev in ("critical", "important", "advisory"):
        items = buckets[sev]
        if not items:
            continue
        _, emoji, label = SEVERITY_META[sev]
        lines.append(f"### {emoji} {label}")
        lines.append("")
        for f in items:
            lines.append(_finding_block(f))
            lines.append("")

    lines.append("---")
    lines.append("_Generated by the `security-review` workflow. Source: `/skills/security-review/` in the fleet command center._")
    lines.append(SECTION_END)

    return "\n".join(lines)


def update_pr_body(current_body: str, section: str | None) -> str:
    """Replace, append, or remove the sentinel-bounded section."""
    pattern = re.compile(
        re.escape(SECTION_START) + r".*?" + re.escape(SECTION_END),
        re.DOTALL,
    )
    has_section = bool(pattern.search(current_body))

    if section is None:
        # Clean state: remove the section if it was present from a prior run.
        if has_section:
            updated = pattern.sub("", current_body)
            # Trim stray blank lines around the removal
            return updated.strip() + "\n" if updated.strip() else ""
        return current_body

    if has_section:
        return pattern.sub(section, current_body)

    # Append with a blank line separator
    if current_body and not current_body.endswith("\n"):
        current_body += "\n"
    return current_body + ("\n" if current_body else "") + section


# --- GitHub API calls -------------------------------------------------------
# Semgrep rule `python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected`
# flags any dynamic URL passed to urllib because urllib supports `file://` schemes.
# In this composer the URL components are:
#   - `repo`: $GITHUB_REPOSITORY (set by GitHub Actions, not user-controlled)
#   - `pr_number`: int-coerced from $PR_NUMBER (also Actions-set; coercion rejects non-int)
# No PR-content-controlled input ever flows into the URL. The scheme is
# hardcoded `https://api.github.com/...`. Suppressing the rule on the two
# urlopen lines below with rationale captured here.

def fetch_pr_body(repo: str, pr_number: int, token: str) -> str:
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    return data.get("body") or ""


def patch_pr_body(repo: str, pr_number: int, token: str, body: str) -> None:
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}"
    payload = json.dumps({"body": body}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="PATCH", headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    })
    # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
    with urllib.request.urlopen(req) as resp:
        if resp.status not in (200, 201):
            raise RuntimeError(f"PATCH failed: HTTP {resp.status}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compose the security-review PR body section and update the PR via GitHub API."
    )
    parser.add_argument("--deterministic-findings", type=Path,
                        default=Path("deterministic-findings.json"))
    parser.add_argument("--semantic-findings", type=Path,
                        default=Path("semantic-findings.json"))
    parser.add_argument("--semantic-skipped", action="store_true",
                        help="Mark Claude semantic review as skipped per §3.3.")
    parser.add_argument("--skip-reason", default="",
                        help="Human-readable reason for the skip (only used with --semantic-skipped).")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""),
                        help="owner/repo (default: $GITHUB_REPOSITORY).")
    parser.add_argument("--pr-number", type=int,
                        default=int(os.environ.get("PR_NUMBER", "0") or 0),
                        help="PR number (default: $PR_NUMBER).")
    parser.add_argument("--github-token", default=os.environ.get("GITHUB_TOKEN", ""),
                        help="GitHub token (default: $GITHUB_TOKEN).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the composed section to stdout instead of patching the PR.")
    args = parser.parse_args()

    det = _load_json(args.deterministic_findings)
    sem = _load_json(args.semantic_findings)

    section = compose_section(det, sem, args.semantic_skipped, args.skip_reason or None)

    if args.dry_run:
        if section is None:
            print("(no section — clean state, nothing to post)")
        else:
            print(section)
        return 0

    if not args.repo or not args.pr_number or not args.github_token:
        print("::error::--repo, --pr-number, and --github-token (or GITHUB_REPOSITORY / PR_NUMBER / GITHUB_TOKEN) are required outside --dry-run",
              file=sys.stderr)
        return 1

    try:
        current = fetch_pr_body(args.repo, args.pr_number, args.github_token)
    except urllib.error.HTTPError as e:
        print(f"::error::GET PR body failed: HTTP {e.code} — {e.reason}", file=sys.stderr)
        return 1

    updated = update_pr_body(current, section)

    if updated == current:
        print("PR body unchanged (no section to post and none present).", file=sys.stderr)
        return 0

    try:
        patch_pr_body(args.repo, args.pr_number, args.github_token, updated)
    except urllib.error.HTTPError as e:
        print(f"::error::PATCH PR body failed: HTTP {e.code} — {e.reason}", file=sys.stderr)
        return 1

    action = "removed" if section is None else "updated"
    print(f"PR body section {action}.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
