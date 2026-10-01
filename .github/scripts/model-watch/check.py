"""model-watch: publish the model catalog to the hub and remind Seth when a pin is behind.

Two outputs, one weekly job (MODEL_SELECTION_PROPOSAL.md §3):

1. CATALOG. Parses every `claude-<family>-N-M` id off the public models overview
   page and POSTs the list to the hub (`/api/v1/models/catalog`), which upserts it
   so the /settings dropdowns can offer exact ids. The hub never deletes, so a
   failed run leaves last-known-good in place.
2. STALENESS. Reads the hub's STORED pins (`/api/v1/models/pins`, the
   `agent_models` settings row) and, if any exact-id pin is behind the newest
   release of its own family, POSTs ONE `follow-up` queue entry (variant
   `model-stale`). Alias pins (`sonnet`, `opus`, ...) never go stale and are
   skipped. A YAML grep remains for the two files with no hub in their trigger
   path (milestone-test-callable.yml, security-review.yml); they pin the alias
   `sonnet`, so it should report nothing.

Deterministic, stdlib-only, no LLM, no API key (the fleet authenticates with
OAuth, so the authenticated /v1/models endpoint is not available; the public
models overview page is the source).

Dedup: request_id hashes (latest id per behind family, sorted behind pins). The
hub returns the existing entry for an identical set, so an unchanged situation
never re-nags; a new release or a changed pin produces a fresh entry.

Exit codes: 0 = current, or entry posted/deduped; 1 = could not determine the
models, could not read the pins, or a POST failed (loud failure). When the model
list cannot be determined NOTHING is posted — no catalog, no queue entry — and
no all-clear is claimed.
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
CATALOG_PATH = "/api/v1/models/catalog"
PINS_PATH = "/api/v1/models/pins"
# A version must be mentioned this many times on the page to count as a real model.
# The models table repeats the current id dozens of times; a stray mention in a
# banner or migration note should not enter the catalog or trigger a reminder.
MIN_MENTIONS = 3
# The only workflows that still carry a YAML pin: no hub in their trigger path.
YAML_PIN_FILES = ("milestone-test-callable.yml", "security-review.yml")

# A YAML pin is either a literal `--model <id>` or the `default: "<id>"` of a `model` input.
PIN_RE = re.compile(r'(?:--model\s+|\bdefault:\s*"?)(claude-[a-z]+-\d+(?:-\d{1,2})?)(?![\w-])')
# Minor is 1-2 digits NOT followed by another digit, so a date suffix
# (claude-sonnet-4-20250514) is read as the date, never as minor 20. The id keeps its date.
PAGE_RE = re.compile(r"claude-([a-z]+)-(\d+)(?:-(\d{1,2})(?!\d))?(-\d{8})?")
ID_RE = re.compile(r"^claude-([a-z]+)-(\d+)(?:-(\d{1,2})(?!\d))?(?:-\d{8})?$")


def parse_id(model_id: str) -> tuple[str, tuple[int, int]] | None:
    """(family, (major, minor)) for an exact model id; None for an alias or anything else."""
    m = ID_RE.match(model_id)
    if not m:
        return None
    return m.group(1), (int(m.group(2)), int(m.group(3) or 0))


def collect_yaml_pins(workflows_dir: Path) -> dict[str, list[str]]:
    """Map pinned exact model id -> file names that pin it, for the two YAML-pinned workflows only."""
    pins: dict[str, list[str]] = {}
    for name in YAML_PIN_FILES:
        path = workflows_dir / name
        if not path.is_file():
            continue
        for model_id in PIN_RE.findall(path.read_text(encoding="utf-8")):
            files = pins.setdefault(model_id, [])
            if name not in files:
                files.append(name)
    return pins


def parse_catalog(html: str) -> list[dict]:
    """Every model id mentioned at least MIN_MENTIONS times, across all families."""
    counts: dict[str, int] = {}
    for m in PAGE_RE.finditer(html):
        counts[m.group(0)] = counts.get(m.group(0), 0) + 1
    out = []
    for model_id in sorted(n for n, c in counts.items() if c >= MIN_MENTIONS):
        family, (major, minor) = parse_id(model_id)  # type: ignore[misc]  # PAGE_RE output always parses
        out.append({"id": model_id, "family": family, "display_name": f"Claude {family.title()} {major}.{minor}"})
    return out


def latest_by_family(catalog: list[dict]) -> dict[str, str]:
    """family -> id of its newest release. A date-suffixed legacy id never outranks a newer version."""
    best: dict[str, tuple[tuple[int, int], str]] = {}
    for model in catalog:
        parsed = parse_id(model["id"])
        if parsed is None:
            continue
        family, version = parsed
        if family not in best or version > best[family][0]:
            best[family] = (version, model["id"])
    return {family: model_id for family, (_, model_id) in best.items()}


def fetch_page(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "aethrix-fleet-ci/1.0 (model-watch)"})
    # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")




def hub_pins(pins_json: dict) -> dict[str, str]:
    """Flatten the hub's `{pins: {<agent>: {model, effort, control}}}` to agent -> model."""
    pins = pins_json.get("pins")
    if not isinstance(pins, dict) or not pins:
        raise ValueError("pins response has no `pins` object")
    return {agent: str(entry["model"]) for agent, entry in pins.items() if isinstance(entry, dict) and entry.get("model")}


def is_behind(model_id: str, latest: dict[str, str]) -> bool:
    """True for an exact id older than the newest release of its family. Aliases never are."""
    parsed = parse_id(model_id)
    if parsed is None:
        return False
    family, version = parsed
    return version < parse_id(latest[family])[1]  # type: ignore[index]  # caller guarantees every pinned family has a latest


def build_packet(latest: dict[str, str], behind: dict[str, str], yaml_behind: dict[str, list[str]], hub_url: str, repo: str) -> dict:
    families = sorted({parse_id(m)[0] for m in [*behind.values(), *yaml_behind]})  # type: ignore[index]
    newest_ids = ", ".join(latest[f] for f in families)
    fingerprint = "|".join(
        ["model-watch", *(f"{f}={latest[f]}" for f in families), *(f"{a}={m}" for a, m in sorted(behind.items())), *sorted(yaml_behind)]
    )
    lines = [f"{agent}: {model_id} (hub setting)" for agent, model_id in sorted(behind.items())]
    lines += [f"{model_id}: {', '.join(files)} (YAML)" for model_id, files in sorted(yaml_behind.items())]
    yaml_note = f"Edit the YAML pins in {repo} too — they should be the alias `sonnet`. " if yaml_behind else ""
    return {
        "request_id": hashlib.sha256(fingerprint.encode("utf-8")).hexdigest(),
        "entry_type": "follow-up",
        "variant": "model-stale",
        "agent_name": "model-watch",
        "title": f"Newer model available: {newest_ids} — {len(lines)} pinned older",
        "goal": f"Agents pinned to an exact model id stay on it until bumped. Anthropic now lists {newest_ids}.",
        "attempts": lines,
        "ask": "Decide whether to bump. Hub pins change on /settings; YAML pins change in the repo.",
        "recommendation": (
            f"Bump each hub pin listed above to {newest_ids} (or switch it to the family alias, which never goes stale) "
            f"in the Agent models section of the hub's /settings. {yaml_note}"
            "Then add a DECISIONS.md entry and smoke-test one run to confirm claude-code-action accepts the id. "
            "Dismiss to stay on the current pin; this entry will not repeat until a newer release or a pin change."
        ),
        "artifacts": [
            {"label": "Agent model settings", "href": hub_url.rstrip("/") + "/settings", "artifact_type": "doc"},
            {"label": "Anthropic models overview", "href": MODELS_URL, "artifact_type": "doc"},
        ],
    }


def _request(url: str, key: str, *, method: str, body: dict | None = None) -> tuple[int, str]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        method=method,
        headers={
            "Content-Type": "application/json",
            "x-service-role-key": key,
            "User-Agent": "aethrix-fleet-ci/1.0 (model-watch)",
        },
    )
    # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


def post_json(hub_url: str, path: str, key: str, body: dict) -> tuple[int, str]:
    return _request(hub_url.rstrip("/") + path, key, method="POST", body=body)


def fetch_pins(hub_url: str, key: str) -> dict:
    _, body = _request(hub_url.rstrip("/") + PINS_PATH, key, method="GET")
    return json.loads(body)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Publish the model catalog to the hub and post a queue reminder when a pin is behind.")
    p.add_argument("--workflows-dir", type=Path, default=Path(".github/workflows"))
    p.add_argument("--models-html", type=Path, default=None, help="Read the models page from a file (tests) instead of fetching it.")
    p.add_argument("--pins-json", type=Path, default=None, help="Read the hub pins response from a file (tests) instead of fetching it.")
    p.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "Aethrix-Labs/.github"))
    p.add_argument("--hub-url", default=os.environ.get("HUB_BASE_URL", DEFAULT_HUB_URL))
    p.add_argument("--service-role-key", default=os.environ.get("QUEUE_SERVICE_ROLE_KEY", ""))
    p.add_argument("--dry-run", action="store_true", help="Print the entry instead of POSTing anything.")
    args = p.parse_args(argv)

    html = args.models_html.read_text(encoding="utf-8") if args.models_html else fetch_page(MODELS_URL)
    catalog = parse_catalog(html)
    latest = latest_by_family(catalog)
    if not latest:
        print("model-watch: could not determine the latest model ids from the models page (layout change?); failing loudly, nothing posted.", file=sys.stderr)
        return 1

    try:
        if args.pins_json:
            raw = json.loads(args.pins_json.read_text(encoding="utf-8"))
        elif args.service_role_key:
            raw = fetch_pins(args.hub_url, args.service_role_key)
        else:
            print("model-watch: QUEUE_SERVICE_ROLE_KEY not set; cannot read the hub's pins.", file=sys.stderr)
            return 1
        pins = hub_pins(raw)
    except (OSError, ValueError, KeyError) as e:  # URLError/HTTPError are OSError; JSONDecodeError is ValueError
        print(f"model-watch: could not read the hub's stored pins ({e}); failing loudly, nothing posted.", file=sys.stderr)
        return 1

    yaml_pins = collect_yaml_pins(args.workflows_dir)
    # An exact-id pin in a family the page no longer lists cannot be verified: refuse the all-clear.
    unverifiable = sorted({m for m in [*pins.values(), *yaml_pins] if (pr := parse_id(m)) is not None and pr[0] not in latest})
    if unverifiable:
        print(f"model-watch: pinned {', '.join(unverifiable)} but the models page lists no release of that family; failing loudly, nothing posted.", file=sys.stderr)
        return 1

    behind = {agent: m for agent, m in pins.items() if is_behind(m, latest)}
    yaml_behind = {m: files for m, files in yaml_pins.items() if is_behind(m, latest)}

    rc = 0
    if args.dry_run:
        print(f"model-watch: [dry-run] would POST a catalog of {len(catalog)} models to {CATALOG_PATH}", file=sys.stderr)
    elif not args.service_role_key:
        print("model-watch: QUEUE_SERVICE_ROLE_KEY not set; cannot publish the catalog.", file=sys.stderr)
        return 1
    else:
        try:
            status, body = post_json(args.hub_url, CATALOG_PATH, args.service_role_key, {"models": catalog})
            print(f"model-watch: catalog POST ({len(catalog)} models) -> {status} {body[:200]}")
        except OSError as e:
            # Keep going: the staleness check does not depend on the catalog landing. The hub keeps last-known-good.
            print(f"model-watch: catalog POST failed ({e}); hub keeps last-known-good.", file=sys.stderr)
            rc = 1

    if not behind and not yaml_behind:
        pinned = ", ".join(f"{a}={m}" for a, m in sorted(pins.items()))
        print(f"model-watch: no exact-id pin is behind its family's latest ({', '.join(sorted(latest.values()))}) — hub pins: {pinned}")
        return rc

    packet = build_packet(latest, behind, yaml_behind, args.hub_url, args.repo)
    if args.dry_run:
        print(json.dumps(packet, indent=2))
        return rc

    try:
        status, body = post_json(args.hub_url, QUEUE_PATH, args.service_role_key, packet)
    except OSError as e:
        print(f"model-watch: queue POST failed ({e}).", file=sys.stderr)
        return 1
    print(f"model-watch: stale pins {', '.join(sorted({*behind.values(), *yaml_behind}))}; queue POST -> {status} {body[:200]}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
