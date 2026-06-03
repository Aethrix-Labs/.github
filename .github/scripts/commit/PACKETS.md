# Commit packets

Reference for every queue entry and activity record the commit skill emits.
`SKILL.md` names a variant; this file is the template. Substitute placeholders only.

## Emission

Queue packets (shape per `STANDARDS.md §8`) POST to `/api/v1/queue/entries`; activity records POST to `/api/v1/activity/entries`. Same headers for both:

```bash
curl -X POST "${HUB_BASE_URL:-https://sethgibson.com}/api/v1/<queue|activity>/entries" \
  -H "Content-Type: application/json" \
  -H "x-service-role-key: $QUEUE_SERVICE_ROLE_KEY" \
  -H "User-Agent: aethrix-fleet-ci/1.0 (commit)" \
  -d '<packet JSON>'
```

Graceful failure on **activity** writes: missing key or non-2xx → log `::warning::`, continue (observability, not gates). Failure handling for **queue** writes is route-specific — see SKILL.md Step 5.

## Common fields

Every packet includes:

```json
{
  "agent_name": "commit",
  "product_slug": "<repo basename>"
}
```

The hub resolves `product_slug` → `product_id` server-side.

## Queue-packet variants

| Variant     | When (SKILL.md ref)       | entry_type | risk_tier          | request_id seed                       |
| ----------- | ------------------------- | ---------- | ------------------ | ------------------------------------- |
| `approval`  | Step 5 Route B            | approval   | `<TIER_FOR_PACKET>` | `sha256(<PR_URL>)` — idempotent on re-run |
| `exception` | Step 5 Route C (no PR)    | exception  | `<TIER>`           | `sha256(<branch> + <timestamp>)`      |

### `approval` (Route B)

```json
{
  "request_id": "<SHA256_OF_PR_URL>",
  "entry_type": "approval",
  "risk_tier": "<TIER_FOR_PACKET>",
  "title": "[<PRODUCT_SLUG>] Ship PR #<PR_NUMBER>: <PR_TITLE>",
  "goal": "Ship PR #<PR_NUMBER>: <PR_TITLE>",
  "attempts": [
    "Adversary loop: <ADVERSARY_SUMMARY>",
    "CI: <CI_STATUS_SUMMARY>",
    "Tests: <TESTS_SUMMARY>"
  ],
  "ask": "Approve this PR for merge?",
  "recommendation": "<RECOMMENDATION>",
  "artifacts": [
    { "label": "PR", "href": "<PR_URL>", "artifact_type": "github-pr" },
    { "label": "PLANNING step", "href": "<PLANNING_ANCHOR_URL>", "artifact_type": "planning-step" }
  ]
}
```

Placeholder rules:

- `<TIER_FOR_PACKET>` = `high` if `<MUST_ESCALATE_HIT> == true`; else `medium` if reached via the CI-failed branch; else `<TIER>` unchanged.
- `<CI_STATUS_SUMMARY>` = `green` if `<CI_STATUS> == pass`; `pending` if `pending`; `red — <failing-check-name>` if `failed`.
- All other placeholders come from the Step 4 output bundle.

### `exception` (Route C)

The PR is NOT opened on Route C — the diff stays on the local branch.

```json
{
  "request_id": "<SHA256_OF_BRANCH_PLUS_TIMESTAMP>",
  "entry_type": "exception",
  "risk_tier": "<TIER>",
  "title": "[<PRODUCT_SLUG>] commit blocked: <ONE_LINE_CAUSE>",
  "goal": "<WHAT_AGENT_WAS_TRYING_TO_DO>",
  "attempts": ["<ATTEMPT_BULLET>", "..."],
  "ask": "<WHAT_SETH_NEEDS_TO_DECIDE>",
  "recommendation": "<OPTIONAL>",
  "artifacts": [
    { "label": "Branch", "href": "<BRANCH_URL>", "artifact_type": "github-branch" },
    { "label": "Adversary report", "href": "<ADVERSARY_REPORT_URL>", "artifact_type": "adversary-report" }
  ]
}
```

Omit the adversary-report artifact when the block isn't adversary-related (e.g. doc-update failure, deploy failure).

## Activity records

The four mid-flight records required by `STANDARDS §9` "Commit-skill exit contract." All carry `"run_id": "$IMPLEMENTER_RUN_ID"` so they stitch into the implementer's `run-started` / `run-completed` bracket — when a run dies mid-commit, these localize the failure to a phase boundary. POST to `/api/v1/activity/entries`.

| Record            | When                                  | request_id seed                          |
| ----------------- | ------------------------------------- | ---------------------------------------- |
| `commit-started`  | On entry, before Step 1               | `sha256(run_id + 'commit-started')`      |
| `tier-classified` | After Step 3 outputs the tier         | `sha256(run_id + 'tier-classified')`     |
| `pr-opened`       | After Step 4d returns the PR URL      | `sha256(run_id + 'pr-opened')`           |
| `commit-exited`   | At exit, on every path                | `sha256(run_id + 'commit-exited')`       |

```json
{
  "request_id": "<per the table>",
  "action": "<commit-started | tier-classified | pr-opened | commit-exited>",
  "run_id": "$IMPLEMENTER_RUN_ID",
  "payload": { "<per-record fields below>" }
}
```

Per-record payloads:

- `commit-started`: `{ "work_item_id": "<M2.7 or BL-12>", "github_run_id": "$GITHUB_RUN_ID" }`
- `tier-classified`: `{ "tier": "<low|medium|high>", "rationale": "<one-line>", "must_escalate_hit": <bool>, "stage": "<stage>" }`
- `pr-opened`: `{ "pr_number": <n>, "pr_url": "<url>", "ci_status": "<pass|pending|failed>" }` — skipped on Route C (no PR).
- `commit-exited`: `{ "exit_reason": "<auto_merge_initiated|queued|exception_emitted>", "pr_url": "<url or null>", "queue_entry_id": "<id or null>" }`
