#!/usr/bin/env python3
"""
aggregator.py — security-review deterministic findings aggregator.

Reads gitleaks, Semgrep, and dep-audit outputs from the security-review CI
workflow and produces a single unified deterministic-findings.json.

Source of truth: /skills/security-review/aggregator.py (sethgibson-com)
Vended to:       .github/scripts/aggregator.py in each product repo
Spec:            /docs/SECURITY_REVIEW_REDESIGN.md §11 v0 plan step 2
Workflow ref:    /skills/security-review/workflow-template.yml

Why this exists
---------------
Each deterministic tool emits its own format (SARIF, custom JSON, etc).
Downstream consumers — the Claude semantic step, the PR body composer when
it lands — should not have to know about three schemas. This script
normalizes everything into one shape.

Output schema (version 1)
-------------------------
{
  "schema_version": "1",
  "generated_at": <ISO 8601 UTC>,
  "summary": {
    "total":     <int>,
    "critical":  <int>,
    "important": <int>,
    "advisory":  <int>,
    "by_tool":   { "gitleaks": <int>, "semgrep": <int>, "dep-audit": <int> }
  },
  "findings": [
    {
      "tool":        "gitleaks" | "semgrep" | "dep-audit",
      "rule_id":     <str>,
      "severity":    "critical" | "important" | "advisory",
      "title":       <str>,
      "description": <str>,
      "file":        <str | null>,
      "line_start":  <int | null>,
      "line_end":    <int | null>,
      "commit_sha":  <str | null>,    # gitleaks only
      "package":     <str | null>,    # dep-audit only
      "raw":         <object>         # original tool-specific payload
    },
    ...
  ]
}

Severity mapping (fleet scale per SECURITY_REVIEW_REDESIGN.md §4)
-----------------------------------------------------------------
- gitleaks:  any finding             -> critical (secrets always block)
- semgrep:   ERROR    -> critical
             WARNING  -> important
             INFO     -> advisory
- dep-audit: critical -> critical
             high     -> important
             moderate, low, info -> advisory

Resilience
----------
Missing or unparseable input files are skipped with a warning printed to
stderr, not aggregated-script failures. Per §12 of the spec: a tool that
fails should not block the workflow on its own absence — it should flag in
the PR body as Advisory. This script realizes that by treating a missing
artifact as "tool emitted zero findings."

Usage
-----
    python3 aggregator.py \\
        --gitleaks gitleaks.sarif \\
        --semgrep semgrep-results.json \\
        --npm-audit npm-audit.json \\
        --output deterministic-findings.json

All flags have sensible defaults matching the workflow's expected filenames.

Stdlib only. Compatible with Python 3.9+.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1"


# --- Severity normalization -------------------------------------------------

def normalize_gitleaks_severity(rule_id: str) -> str:
    """Gitleaks doesn't tier its findings — a committed secret is always Critical."""
    return "critical"


def normalize_semgrep_severity(severity: str) -> str:
    s = (severity or "").upper()
    if s == "ERROR":
        return "critical"
    if s == "WARNING":
        return "important"
    return "advisory"


def normalize_npm_audit_severity(severity: str) -> str:
    s = (severity or "").lower()
    if s == "critical":
        return "critical"
    if s == "high":
        return "important"
    return "advisory"


# --- Parsers ----------------------------------------------------------------

def _load_json(path: Path, label: str) -> Any:
    """Read JSON safely; return None on any failure with a CI-formatted warning."""
    if not path.exists():
        print(f"::warning::{label} input not found at {path}; treating as zero findings.",
              file=sys.stderr)
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"::warning::{label} input at {path} is not valid JSON ({e}); "
              f"treating as zero findings.", file=sys.stderr)
        return None


def _finding(tool: str, rule_id: str, severity: str, title: str,
             description: str, file: str | None = None,
             line_start: int | None = None, line_end: int | None = None,
             commit_sha: str | None = None, package: str | None = None,
             raw: Any = None) -> dict:
    """Canonical finding shape — all parsers build via this helper."""
    return {
        "tool": tool,
        "rule_id": rule_id,
        "severity": severity,
        "title": title,
        "description": description,
        "file": file,
        "line_start": line_start,
        "line_end": line_end,
        "commit_sha": commit_sha,
        "package": package,
        "raw": raw,
    }


def parse_gitleaks(path: Path) -> list[dict]:
    """Parse gitleaks SARIF output."""
    data = _load_json(path, "gitleaks")
    if data is None:
        return []

    findings: list[dict] = []
    for run in data.get("runs", []) or []:
        for result in run.get("results", []) or []:
            rule_id = result.get("ruleId") or "unknown"
            message = (result.get("message") or {}).get("text") or ""
            commit_sha = (result.get("properties") or {}).get("commit")
            for loc in result.get("locations", []) or []:
                phys = loc.get("physicalLocation") or {}
                artifact = phys.get("artifactLocation") or {}
                region = phys.get("region") or {}
                findings.append(_finding(
                    tool="gitleaks",
                    rule_id=rule_id,
                    severity=normalize_gitleaks_severity(rule_id),
                    title=f"Secret detected ({rule_id})",
                    description=message,
                    file=artifact.get("uri"),
                    line_start=region.get("startLine"),
                    line_end=region.get("endLine"),
                    commit_sha=commit_sha,
                    raw=result,
                ))
    return findings


def parse_semgrep(path: Path) -> list[dict]:
    """Parse Semgrep output — handles both native JSON and SARIF shapes."""
    data = _load_json(path, "semgrep")
    if data is None:
        return []

    findings: list[dict] = []

    # SARIF shape (Semgrep can emit either).
    if isinstance(data, dict) and "runs" in data:
        for run in data.get("runs", []) or []:
            for result in run.get("results", []) or []:
                rule_id = result.get("ruleId") or "unknown"
                level = result.get("level") or "warning"
                message = (result.get("message") or {}).get("text") or ""
                for loc in result.get("locations", []) or []:
                    phys = loc.get("physicalLocation") or {}
                    artifact = phys.get("artifactLocation") or {}
                    region = phys.get("region") or {}
                    findings.append(_finding(
                        tool="semgrep",
                        rule_id=rule_id,
                        severity=normalize_semgrep_severity(level),
                        title=rule_id,
                        description=message,
                        file=artifact.get("uri"),
                        line_start=region.get("startLine"),
                        line_end=region.get("endLine"),
                        raw=result,
                    ))
        return findings

    # Native Semgrep JSON shape.
    for result in (data.get("results", []) if isinstance(data, dict) else []) or []:
        extra = result.get("extra") or {}
        rule_id = result.get("check_id") or "unknown"
        severity = extra.get("severity") or "WARNING"
        message = extra.get("message") or ""
        start = result.get("start") or {}
        end = result.get("end") or {}
        findings.append(_finding(
            tool="semgrep",
            rule_id=rule_id,
            severity=normalize_semgrep_severity(severity),
            title=rule_id,
            description=message,
            file=result.get("path"),
            line_start=start.get("line"),
            line_end=end.get("line"),
            raw=result,
        ))
    return findings


def parse_npm_audit(path: Path) -> list[dict]:
    """Parse npm audit v2+ JSON output (the vulnerabilities-object shape)."""
    data = _load_json(path, "npm audit")
    if data is None:
        return []

    findings: list[dict] = []
    vulns = data.get("vulnerabilities") if isinstance(data, dict) else None
    if not isinstance(vulns, dict):
        return findings

    for pkg_name, vuln_info in vulns.items():
        if not isinstance(vuln_info, dict):
            continue
        severity = vuln_info.get("severity") or "low"
        for via in vuln_info.get("via", []) or []:
            if isinstance(via, dict):
                title = via.get("title") or f"Vulnerability in {pkg_name}"
                rule_id = str(via.get("source") or "unknown")
                description = via.get("url") or ""
                findings.append(_finding(
                    tool="dep-audit",
                    rule_id=rule_id,
                    severity=normalize_npm_audit_severity(severity),
                    title=title,
                    description=description,
                    file="package-lock.json",
                    package=pkg_name,
                    raw=via,
                ))
            elif isinstance(via, str):
                # `via` can be a string referring to another package in the tree.
                findings.append(_finding(
                    tool="dep-audit",
                    rule_id="transitive",
                    severity=normalize_npm_audit_severity(severity),
                    title=f"Transitive vulnerability via {via} in {pkg_name}",
                    description="",
                    file="package-lock.json",
                    package=pkg_name,
                    raw={"via": via},
                ))
    return findings


# --- Summarization & output -------------------------------------------------

def summarize(findings: list[dict]) -> dict:
    by_tool: dict[str, int] = {}
    by_severity = {"critical": 0, "important": 0, "advisory": 0}
    for f in findings:
        by_tool[f["tool"]] = by_tool.get(f["tool"], 0) + 1
        sev = f["severity"]
        if sev in by_severity:
            by_severity[sev] += 1
    return {
        "total": len(findings),
        "critical": by_severity["critical"],
        "important": by_severity["important"],
        "advisory": by_severity["advisory"],
        "by_tool": by_tool,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate deterministic security-review findings into unified JSON."
    )
    parser.add_argument(
        "--gitleaks", type=Path, default=Path("gitleaks.sarif"),
        help="Path to gitleaks SARIF output (default: gitleaks.sarif)",
    )
    parser.add_argument(
        "--semgrep", type=Path, default=Path("semgrep-results.json"),
        help="Path to Semgrep output, JSON or SARIF (default: semgrep-results.json)",
    )
    parser.add_argument(
        "--npm-audit", type=Path, default=Path("npm-audit.json"),
        help="Path to npm audit JSON output (default: npm-audit.json)",
    )
    parser.add_argument(
        "--output", type=Path, default=Path("deterministic-findings.json"),
        help="Path to write unified output (default: deterministic-findings.json)",
    )
    args = parser.parse_args()

    findings: list[dict] = []
    findings.extend(parse_gitleaks(args.gitleaks))
    findings.extend(parse_semgrep(args.semgrep))
    findings.extend(parse_npm_audit(args.npm_audit))

    output = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summarize(findings),
        "findings": findings,
    }

    args.output.write_text(json.dumps(output, indent=2) + "\n")

    s = output["summary"]
    print(
        f"aggregator: wrote {args.output} — "
        f"total={s['total']} critical={s['critical']} "
        f"important={s['important']} advisory={s['advisory']} "
        f"by_tool={s['by_tool']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
