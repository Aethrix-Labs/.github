#!/usr/bin/env python3
"""
should-run-semantic.py — §3.3 conditional gate for the security-review semantic step.

Reads the PR diff scope + the upstream deterministic-findings.json and decides
whether to invoke the Claude semantic step on this PR.

Source of truth: /skills/security-review/should-run-semantic.py (sethgibson-com)
Vended to:       .github/scripts/security-review/should-run-semantic.py in each product repo
Spec:            /docs/SECURITY_REVIEW_REDESIGN.md §3.3 (conditional execution rules)
Workflow ref:    /skills/security-review/workflow-template.yml (semantic job)

Decision rule (run if ANY)
--------------------------
1. The diff touches security-relevant paths (routes, middleware, auth files,
   schema files, payment/billing files — patterns below).
2. Deterministic tools (gitleaks + Semgrep + dep-audit, aggregated) found at
   least one finding above Advisory tier.
3. The diff is significant (>10 files OR >300 lines changed) AND any source
   code file extension is in the diff.

If none of those fire, skip Claude. The cost of skipping incorrectly is one
PR's worth of semantic blind spot; the cost of running unnecessarily is
real API spend. The deterministic tools still ran and still gate on Critical.

Output
------
Writes to $GITHUB_OUTPUT:
    run=true|false
    reasons=<multi-line description of triggers fired or skip rationale>

Also prints a human-readable summary to stdout for CI log visibility.

Stdlib only. Compatible with Python 3.9+.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Path patterns considered security-relevant. Matched against forward-slash
# file paths from `git diff --name-only`. Case-insensitive.
SECURITY_PATH_PATTERNS = [
    r".*/routes/.*",
    r".*/api/.*",
    r".*/controllers/.*",
    r".*/middleware/.*",
    r".*/guards/.*",
    r".*auth.*",
    r".*login.*",
    r".*session.*",
    r"prisma/schema\.prisma",
    r".*\.sql$",
    r".*/migrations/.*",
    r".*billing.*",
    r".*payment.*",
    r".*stripe.*",
]

# Source-code extensions used by the "large diff" rule's source-code-present check.
SOURCE_EXTS = (
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".py", ".go", ".rs", ".java", ".kt",
    ".rb", ".php", ".swift", ".m", ".mm",
    ".c", ".cc", ".cpp", ".h", ".hpp",
    ".cs", ".scala", ".clj", ".ex", ".exs",
)


def _git(args: list[str]) -> str:
    """Run a git command, return stdout. Empty string on failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            print(f"::warning::git {' '.join(args)} failed: {result.stderr.strip()}",
                  file=sys.stderr)
            return ""
        return result.stdout
    except FileNotFoundError:
        print("::error::git executable not found", file=sys.stderr)
        return ""


def changed_files(base: str, head: str) -> list[str]:
    out = _git(["diff", "--name-only", f"{base}...{head}"])
    return [line.strip() for line in out.splitlines() if line.strip()]


def diff_size(base: str, head: str) -> tuple[int, int]:
    """Returns (files_changed, total_lines_changed)."""
    out = _git(["diff", "--shortstat", f"{base}...{head}"]).strip()
    # Format: " 5 files changed, 100 insertions(+), 20 deletions(-)"
    files_m = re.search(r"(\d+) files? changed", out)
    insertions_m = re.search(r"(\d+) insertions?\(\+\)", out)
    deletions_m = re.search(r"(\d+) deletions?\(-\)", out)
    files = int(files_m.group(1)) if files_m else 0
    lines = (int(insertions_m.group(1)) if insertions_m else 0) + \
            (int(deletions_m.group(1)) if deletions_m else 0)
    return files, lines


def security_path_hit(files: list[str]) -> tuple[str | None, str | None]:
    """Returns (matched_file, matched_pattern) or (None, None)."""
    for f in files:
        for pat in SECURITY_PATH_PATTERNS:
            if re.match(pat, f, re.IGNORECASE):
                return f, pat
    return None, None


def deterministic_above_advisory(path: Path) -> tuple[bool, str]:
    """Returns (has_above_advisory, human_reason)."""
    if not path.exists():
        return False, f"{path} not found"
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"::warning::could not parse {path}: {e}; treating as zero findings.",
              file=sys.stderr)
        return False, f"{path} unparseable"

    summary = data.get("summary") or {}
    critical = int(summary.get("critical", 0) or 0)
    important = int(summary.get("important", 0) or 0)
    if critical + important > 0:
        return True, f"critical={critical} important={important}"
    advisory = int(summary.get("advisory", 0) or 0)
    return False, f"only advisory ({advisory}) or empty"


def has_source_code(files: list[str]) -> bool:
    return any(f.endswith(SOURCE_EXTS) for f in files)


def emit_output(key: str, value: str) -> None:
    """Write a key=value (or key<<EOF block for multi-line) to $GITHUB_OUTPUT."""
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        # Local execution / smoke test — just print
        print(f"[GITHUB_OUTPUT] {key}={value}", file=sys.stderr)
        return
    with open(output_path, "a") as fp:
        if "\n" in value:
            fp.write(f"{key}<<EOF\n{value}\nEOF\n")
        else:
            fp.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="§3.3 conditional gate for the semantic security-review step."
    )
    parser.add_argument("--base", required=True,
                        help="Base SHA / ref of the diff (e.g., PR base sha).")
    parser.add_argument("--head", required=True,
                        help="Head SHA / ref of the diff (e.g., PR head sha).")
    parser.add_argument("--deterministic-findings", type=Path,
                        default=Path("deterministic-findings.json"),
                        help="Path to upstream aggregator output (default: deterministic-findings.json).")
    args = parser.parse_args()

    files = changed_files(args.base, args.head)
    n_files, n_lines = diff_size(args.base, args.head)

    sec_file, sec_pat = security_path_hit(files)
    findings_hit, findings_reason = deterministic_above_advisory(args.deterministic_findings)
    large = n_files > 10 or n_lines > 300
    sourceful = has_source_code(files)

    reasons: list[str] = []
    if sec_file:
        reasons.append(f"security path touched: {sec_file} (pattern: {sec_pat})")
    if findings_hit:
        reasons.append(f"deterministic findings above advisory: {findings_reason}")
    if large and sourceful:
        reasons.append(f"large diff with source code: {n_files} files, {n_lines} total lines changed")

    should_run = bool(reasons)

    if should_run:
        summary_line = f"RUN — {len(reasons)} trigger(s) fired"
        body = "\n".join(f"  - {r}" for r in reasons)
    else:
        summary_line = "SKIP — no triggers fired"
        body = (f"  - diff doesn't touch security-relevant paths\n"
                f"  - {findings_reason}\n"
                f"  - diff size: {n_files} files, {n_lines} lines (large={large}, has_source={sourceful})")

    print(f"should-run-semantic: {summary_line}")
    print(body)

    emit_output("run", "true" if should_run else "false")
    emit_output("reasons", "\n".join(reasons) if reasons else "no triggers fired")

    return 0


if __name__ == "__main__":
    sys.exit(main())
