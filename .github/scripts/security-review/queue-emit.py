#!/usr/bin/env python3
"""
queue-emit.py — security-review queue emission to the hub's decision queue.

Reads `deterministic-findings.json` and `semantic-findings.json` from the CI
workspace (plus workflow context env vars) and POSTs an `exception` entry to
the hub's queue when a scenario warrants Seth's attention. Default is silent;
only two scenarios fire:

  1. Any Critical finding (deterministic or semantic).
     → `exception` entry, risk_tier: "high". Surfaces the merge block.

  2. Semantic step was required by §3.3 but failed (Claude API error,
     OAuth token issue, etc.).
     → `exception` entry, risk_tier: "medium". Per spec §12.

Clean runs, Important/Advisory-only runs, and legitimate semantic skips
(per §3.3 conditional gate) produce NO queue entry. The PR body composer
handles user-visible reporting for those.

Source of truth: /skills/security-review/queue-emit.py (sethgibson-com)
Vended to:       Aethrix-Labs/.github:.github/scripts/security-review/queue-emit.py
                 (promoted via the §17.5 drift/update protocol)
Spec:            /docs/SECURITY_REVIEW_REDESIGN.md §11 v0 plan step 6
Hub API:         POST /api/v1/queue/entries (M3, shipped 2026-05-20)
Workflow ref:    Aethrix-Labs/.github:.github/workflows/security-review.yml

Auth
----
Requires QUEUE_SERVICE_ROLE_KEY (org-level GitHub secret in Aethrix-Labs;
local override via --service-role-key for dry-run testing). If the secret
is missing, the script logs ::warning:: and exits 0 — never fails the
workflow on queue infrastructure hiccups.

Idempotency
-----------
request_id = sha256(repo + PR# + commit_sha + scenario_key)

Re-runs on the same commit produce the same request_id; the hub's M3
API dedups server-side (returns 200 with existing entry id). New commits
to the same PR get fresh entries because findings can change with new code.

Stdlib only. Compatible with Python 3.9+.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_HUB_URL = "https://sethgibson.com"
QUEUE_PATH = "/api/v1/queue/entries"

# Severity buckets we care about for queue emission.
CRITICAL = "critical"


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _load_json(path: Path) -> dict | None:
    """Load a findings JSON. Missing file → None (treated as 'no findings')."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"::warning::could not parse {path}: {e}", file=sys.stderr)
        return None


def _summary_count(findings_doc: dict | None, severity: str) -> int:
    """Pull severity count from a findings doc's summary block; 0 if missing."""
    if not findings_doc:
        return 0
    summary = findings_doc.get("summary") or {}
    return int(summary.get(severity, 0) or 0)


def _top_critical(findings_doc: dict | None) -> dict | None:
    """Return the first Critical finding from a findings doc, if any."""
    if not findings_doc:
        return None
    for f in findings_doc.get("findings", []) or []:
        if (f.get("severity") or "").lower() == CRITICAL:
            return f
    return None


def _short(text: str, limit: int = 120) -> str:
    """Truncate a string for use in titles/asks/recommendations."""
    text = (text or "").strip().replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _slug_from_repo(repo: str) -> str:
    """Extract the product slug from owner/repo."""
    return repo.split("/", 1)[1] if "/" in repo else repo


def _request_id(repo: str, pr_number: int, commit_sha: str, scenario_key: str) -> str:
    """Stable per (repo, PR, commit, scenario)."""
    raw = f"{repo}|{pr_number}|{commit_sha}|{scenario_key}".encode("utf-8")
    return f"security-review-{hashlib.sha256(raw).hexdigest()[:32]}"


def _artifacts(server_url: str, repo: str, pr_number: int, run_id: str) -> list[dict]:
    pr_url = f"{server_url}/{repo}/pull/{pr_number}"
    run_url = f"{server_url}/{repo}/actions/runs/{run_id}" if run_id else None
    out = [{"label": "PR", "href": pr_url, "artifact_type": "github-pr"}]
    if run_url:
        out.append({"label": "CI run", "href": run_url, "artifact_type": "github-actions-run"})
    return out


# ─── Packet composition ───────────────────────────────────────────────────────


def _compose_critical_packet(
    *,
    repo: str,
    pr_number: int,
    commit_sha: str,
    run_id: str,
    server_url: str,
    det: dict | None,
    sem: dict | None,
) -> dict:
    slug = _slug_from_repo(repo)
    det_crit = _summary_count(det, CRITICAL)
    sem_crit = _summary_count(sem, CRITICAL)
    det_imp = _summary_count(det, "important")
    sem_imp = _summary_count(sem, "important")

    total_crit = det_crit + sem_crit
    sources = []
    if det_crit:
        sources.append(f"{det_crit} deterministic")
    if sem_crit:
        sources.append(f"{sem_crit} semantic")
    sources_str = " + ".join(sources)

    top = _top_critical(sem) or _top_critical(det)
    top_summary = ""
    if top:
        tool = top.get("tool") or "?"
        title = top.get("title") or top.get("rule_id") or "(no title)"
        loc = top.get("file") or ""
        line = top.get("line_start")
        loc_str = f" at {loc}:{line}" if loc and line else (f" at {loc}" if loc else "")
        top_summary = f"{tool}: {_short(title, 80)}{loc_str}"

    attempts = [
        f"Deterministic: {det_crit} critical, {det_imp} important",
        f"Semantic: {sem_crit} critical, {sem_imp} important",
    ]
    if top_summary:
        attempts.append(f"Top finding — {top_summary}")

    recommendation = (
        f"Fix the Critical finding{'s' if total_crit > 1 else ''} before merging. "
        "If the finding is a confirmed false positive that the deterministic tool's "
        "config doesn't catch, add an override per the triage convention in SKILL.md."
    )

    return {
        "request_id": _request_id(repo, pr_number, commit_sha, "critical"),
        "entry_type": "exception",
        "risk_tier": "high",
        "agent_name": "security-review",
        "title": _short(
            f"[{slug}] Critical security findings in PR #{pr_number} ({sources_str})",
            200,
        ),
        "goal": f"Run security-review on PR #{pr_number} and gate the merge",
        "attempts": attempts,
        "ask": "Review the Critical findings; fix and re-push, or override with a triage entry if confirmed false-positive.",
        "recommendation": _short(recommendation, 500),
        "artifacts": _artifacts(server_url, repo, pr_number, run_id),
    }


def _compose_semantic_fail_packet(
    *,
    repo: str,
    pr_number: int,
    commit_sha: str,
    run_id: str,
    server_url: str,
    fail_reason: str,
) -> dict:
    slug = _slug_from_repo(repo)
    return {
        "request_id": _request_id(repo, pr_number, commit_sha, "semantic-fail"),
        "entry_type": "exception",
        "risk_tier": "medium",
        "agent_name": "security-review",
        "title": _short(
            f"[{slug}] security-review semantic step failed on PR #{pr_number}", 200
        ),
        "goal": f"Run the semantic security checks on PR #{pr_number}",
        "attempts": [
            f"Semantic step: failed — {_short(fail_reason or 'no reason supplied', 200)}",
            "Conditional gate said this step was required for this PR's diff (per §3.3).",
        ],
        "ask": "Re-run the workflow (transient failure) or investigate (persistent failure: token/quota/skill issue).",
        "recommendation": (
            "Retry once. If it fails again, check the CLAUDE_CODE_OAUTH_TOKEN "
            "status per STANDARDS §14 OAuth token rotation and the Anthropic "
            "subscription quota / usage-credit wallet (per §14 Billing buckets)."
        ),
        "artifacts": _artifacts(server_url, repo, pr_number, run_id),
    }


# ─── Scenario decision ────────────────────────────────────────────────────────


def _decide_scenario(
    *, det: dict | None, sem: dict | None, semantic_failed: bool
) -> str:
    """Return one of: 'critical' | 'semantic-fail' | 'silent'."""
    if _summary_count(det, CRITICAL) or _summary_count(sem, CRITICAL):
        # Critical findings take precedence — they block merge regardless of
        # what else happened. A separate semantic-fail entry would be noise.
        return "critical"
    if semantic_failed:
        return "semantic-fail"
    return "silent"


# ─── POST to the hub ──────────────────────────────────────────────────────────


def _post_entry(hub_url: str, service_role_key: str, packet: dict) -> tuple[int, str]:
    """POST the packet. Returns (status_code, body_text). Raises on transport error."""
    url = hub_url.rstrip("/") + QUEUE_PATH
    body = json.dumps(packet).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-service-role-key": service_role_key,
            # Explicit User-Agent — Python urllib's default `Python-urllib/3.x`
            # gets caught by Cloudflare Bot Fight Mode (error 1010). Surfaced
            # 2026-05-20 on the first implementer-loop fire on puzzle-pop PR #6
            # — security-review's queue-emit got 1010'd while the commit skill's
            # Node-based POST went through cleanly. Setting an identifiable UA
            # avoids the heuristic without needing a Cloudflare rule change.
            # See STANDARDS §11.3 milestone entry, drift #3.
            "User-Agent": "aethrix-fleet-ci/1.0 (security-review queue-emit)",
        },
    )
    # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


# ─── Main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Emit a security-review queue entry to the hub when warranted."
    )
    parser.add_argument(
        "--deterministic-findings", type=Path, default=Path("deterministic-findings.json")
    )
    parser.add_argument(
        "--semantic-findings", type=Path, default=Path("semantic-findings.json")
    )
    parser.add_argument(
        "--semantic-failed",
        action="store_true",
        help="Mark the semantic step as having failed (required-but-errored case).",
    )
    parser.add_argument(
        "--fail-reason",
        default="",
        help="Human-readable reason for the semantic failure (pairs with --semantic-failed).",
    )
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY", ""),
        help="owner/repo (default: $GITHUB_REPOSITORY).",
    )
    parser.add_argument(
        "--pr-number",
        type=int,
        default=int(os.environ.get("PR_NUMBER", "0") or 0),
        help="PR number (default: $PR_NUMBER).",
    )
    parser.add_argument(
        "--commit-sha",
        default=os.environ.get("COMMIT_SHA", "") or os.environ.get("GITHUB_SHA", ""),
        help="Commit SHA under review (default: $COMMIT_SHA or $GITHUB_SHA).",
    )
    parser.add_argument(
        "--run-id",
        default=os.environ.get("GITHUB_RUN_ID", ""),
        help="GitHub Actions run id (default: $GITHUB_RUN_ID).",
    )
    parser.add_argument(
        "--server-url",
        default=os.environ.get("GITHUB_SERVER_URL", "https://github.com"),
        help="GitHub server URL (default: $GITHUB_SERVER_URL).",
    )
    parser.add_argument(
        "--hub-url",
        default=os.environ.get("HUB_BASE_URL", DEFAULT_HUB_URL),
        help=f"Hub base URL (default: $HUB_BASE_URL or {DEFAULT_HUB_URL}).",
    )
    parser.add_argument(
        "--service-role-key",
        default=os.environ.get("QUEUE_SERVICE_ROLE_KEY", ""),
        help="Hub service-role key (default: $QUEUE_SERVICE_ROLE_KEY).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the packet to stdout instead of POSTing.",
    )
    args = parser.parse_args()

    det = _load_json(args.deterministic_findings)
    sem = _load_json(args.semantic_findings)

    scenario = _decide_scenario(det=det, sem=sem, semantic_failed=args.semantic_failed)

    if scenario == "silent":
        print("queue-emit: no entry needed (no Critical findings, no semantic failure).")
        return 0

    if not args.repo or not args.pr_number or not args.commit_sha:
        print(
            "::warning::queue-emit: --repo, --pr-number, and --commit-sha required "
            "(or $GITHUB_REPOSITORY / $PR_NUMBER / $COMMIT_SHA / $GITHUB_SHA). "
            "Skipping queue write.",
            file=sys.stderr,
        )
        return 0

    if scenario == "critical":
        packet = _compose_critical_packet(
            repo=args.repo,
            pr_number=args.pr_number,
            commit_sha=args.commit_sha,
            run_id=args.run_id,
            server_url=args.server_url,
            det=det,
            sem=sem,
        )
    else:  # semantic-fail
        packet = _compose_semantic_fail_packet(
            repo=args.repo,
            pr_number=args.pr_number,
            commit_sha=args.commit_sha,
            run_id=args.run_id,
            server_url=args.server_url,
            fail_reason=args.fail_reason,
        )

    if args.dry_run:
        print(json.dumps(packet, indent=2))
        return 0

    if not args.service_role_key:
        print(
            "::warning::queue-emit: QUEUE_SERVICE_ROLE_KEY not set; skipping queue write. "
            f"(Would have posted scenario={scenario} for {args.repo}#{args.pr_number}.)",
            file=sys.stderr,
        )
        return 0

    try:
        status, body = _post_entry(args.hub_url, args.service_role_key, packet)
    except urllib.error.HTTPError as e:
        # Server reported an HTTP error (4xx/5xx). Read body for context.
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        print(
            f"::warning::queue-emit: POST failed: HTTP {e.code} {e.reason} — {err_body[:300]}",
            file=sys.stderr,
        )
        return 0
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"::warning::queue-emit: POST transport error: {e}", file=sys.stderr)
        return 0

    if status not in (200, 201):
        print(f"::warning::queue-emit: unexpected status {status} — {body[:300]}", file=sys.stderr)
        return 0

    # Parse the response to grab the entry id for the log line.
    entry_id = ""
    try:
        entry_id = (json.loads(body) or {}).get("id", "")
    except json.JSONDecodeError:
        pass

    action = "created" if status == 201 else "deduped (request_id already seen)"
    print(f"queue-emit: scenario={scenario} {action} entry_id={entry_id or '?'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
