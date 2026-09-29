#!/usr/bin/env python3
"""model-watch: remind Seth when a fleet workflow is pinned to an older Sonnet.

STANDARDS §14 requires every autonomous agent to pin its model explicitly, so
pins never move on their own. This script is the reminder surface: it reads
the pins from the workflow files in this repo (ground truth, no registry to
drift), fetches the newest Sonnet id Anthropic publishes, and — if any pin is
behind — POSTs ONE `follow-up` queue entry to the hub.

Deterministic, stdlib-only, no LLM, no API key (the fleet authenticates with
OAuth, so the authenticated /v1/models endpoint is not available; the public
models overview page is the source).

Dedup: request_id hashes (latest Sonnet, sorted pins). The hub returns the
existing entry for an identical set, so an unchanged situation never re-nags;
a new release or a changed pin set produces a fresh entry.

Exit codes: 0 = current or entry posted/deduped; 1 = could not determine the
latest model or found no pins (loud failure, nothing posted).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

MODELS_URL = "https://platform.claude.com/docs/en/about-claude/models/overview"
DEFAULT_HUB_URL = "https://sethgibson.com"
QUEUE_PATH = "/api/v1/queue/entries"
FAMILY = "sonnet"
# A version must be mentioned this many times on the page to count as "latest".
# The models table repeats the current id dozens of times; a stray mention in a
# banner or migration note should not trigger a reminder.
MIN_MENTIONS = 3

# A pin is either a literal `--model <id>` or the `default: "<id>"` of a callable's `model` input
# (the hub overrides that input per run; the default is the fleet-wide fallback that goes stale).
PIN_RE = re.compile(r'(?:--model\s+|\bdefault:\s*"?)(claude-[a-z]+-\d+(?:-\d{1,2})?)(?![\w-])')
# Minor is 1-2 digits NOT followed by another digit, so date-suffixed ids
# (claude-sonnet-4-20250514) parse as major-only instead of 4.20.
PAGE_RE = re.compile(rf"claude-{FAMILY}-(\d+)(?:-(\d{{1,2}})(?!\d))?")
ID_RE = re.compile(rf"claude-{FAMILY}-(\d+)(?:-(\d{{1,2}}))?$")


def parse_version(model_id: str) -> tuple[int, int] | None:
    m = ID_RE.match(model_id)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2) or 0)


def fmt(version: tuple[int, int]) -> str:
    return f"claude-{FAMILY}-{version[0]}-{version[1]}"


def collect_pins(workflows_dir: Path) -> dict[str, list[str]]:
    """Map pinned model id -> workflow file names that pin it."""
    pins: dict[str, list[str]] = {}
    for path in sorted(workflows_dir.glob("*.yml")):
        for model_id in PIN_RE.findall(path.read_text(encoding="utf-8")):
            files = pins.setdefault(model_id, [])
            if path.name not in files:
                files.append(path.name)
    return pins


def latest_from_page(html: str) -> tuple[int, int] | None:
    counts: dict[tuple[int, int], int] = {}
    for m in PAGE_RE.finditer(html):
        v = (int(m.group(1)), int(m.group(2) or 0))
        counts[v] = counts.get(v, 0) + 1
    eligible = [v for v, n in counts.items() if n >= MIN_MENTIONS]
    return max(eligible) if eligible else None


def fetch_page(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "aethrix-fleet-ci/1.0 (model-watch)"})
    # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def behind_pins(pins: dict[str, list[str]], latest: tuple[int, int]) -> dict[str, list[str]]:
    out = {}
    for model_id, files in pins.items():
        v = parse_version(model_id)
        if v is not None and v < latest:
            out[model_id] = files
    return out


def build_packet(latest: tuple[int, int], pins: dict[str, list[str]], behind: dict[str, list[str]], repo: str) -> dict:
    latest_id = fmt(latest)
    fingerprint = f"model-watch|{latest_id}|{'|'.join(sorted(pins))}"
    lines = [f"{model_id}: {', '.join(files)}" for model_id, files in sorted(behind.items())]
    workflows_url = f"https://github.com/{repo}/tree/main/.github/workflows"
    return {
        "request_id": hashlib.sha256(fingerprint.encode("utf-8")).hexdigest(),
        "entry_type": "follow-up",
        "agent_name": "model-watch",
        "title": f"Newer Sonnet available: {latest_id} — {len(behind)} pinned older",
        "goal": f"Fleet workflows pin their model explicitly (STANDARDS §14), so they stay on an older Sonnet until bumped. Anthropic now lists {latest_id}.",
        "attempts": lines,
        "ask": "Decide whether to bump. To do it, paste the recommendation into a session in the fleet repo (~/products).",
        "recommendation": (
            f"Bump the Sonnet pins to {latest_id}: edit the `--model` lines and the `model` input defaults in {repo} workflows listed above, "
            "then update the STANDARDS §14 per-agent matrix and add a DECISIONS.md entry. "
            "Smoke-test one implementer run afterwards to confirm claude-code-action accepts the id. "
            "Dismiss to stay on the current pin; this entry will not repeat until a newer release or a pin change."
        ),
        "artifacts": [
            {"label": "Workflow pins", "href": workflows_url, "artifact_type": "doc"},
            {"label": "Anthropic models overview", "href": MODELS_URL, "artifact_type": "doc"},
        ],
    }


def post_entry(hub_url: str, key: str, packet: dict) -> tuple[int, str]:
    req = urllib.request.Request(
        hub_url.rstrip("/") + QUEUE_PATH,
        data=json.dumps(packet).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-service-role-key": key,
            "User-Agent": "aethrix-fleet-ci/1.0 (model-watch)",
        },
    )
    # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Post a queue reminder when a workflow pins an older Sonnet.")
    p.add_argument("--workflows-dir", type=Path, default=Path(".github/workflows"))
    p.add_argument("--models-html", type=Path, default=None, help="Read the models page from a file (tests) instead of fetching it.")
    p.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "Aethrix-Labs/.github"))
    p.add_argument("--hub-url", default=os.environ.get("HUB_BASE_URL", DEFAULT_HUB_URL))
    p.add_argument("--service-role-key", default=os.environ.get("QUEUE_SERVICE_ROLE_KEY", ""))
    p.add_argument("--dry-run", action="store_true", help="Print the entry instead of POSTing it.")
    args = p.parse_args(argv)

    pins = collect_pins(args.workflows_dir)
    sonnet_pins = {m: f for m, f in pins.items() if parse_version(m) is not None}
    if not sonnet_pins:
        print(f"model-watch: no Sonnet --model pins found under {args.workflows_dir}; refusing to report all-clear.", file=sys.stderr)
        return 1

    html = args.models_html.read_text(encoding="utf-8") if args.models_html else fetch_page(MODELS_URL)
    latest = latest_from_page(html)
    if latest is None:
        print(f"model-watch: could not determine the latest {FAMILY} id from the models page (layout change?); failing loudly.", file=sys.stderr)
        return 1

    behind = behind_pins(sonnet_pins, latest)
    pinned = ", ".join(f"{m} ({len(f)})" for m, f in sorted(sonnet_pins.items()))
    if not behind:
        print(f"model-watch: all {FAMILY} pins current at {fmt(latest)} — pinned: {pinned}")
        return 0

    packet = build_packet(latest, sonnet_pins, behind, args.repo)
    if args.dry_run:
        print(json.dumps(packet, indent=2))
        return 0

    if not args.service_role_key:
        print("model-watch: QUEUE_SERVICE_ROLE_KEY not set; cannot post the reminder.", file=sys.stderr)
        return 1

    status, body = post_entry(args.hub_url, args.service_role_key, packet)
    print(f"model-watch: {fmt(latest)} is newer than {', '.join(sorted(behind))}; queue POST -> {status} {body[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
