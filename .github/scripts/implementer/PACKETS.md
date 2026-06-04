# Implementer packets

Reference for every queue entry and activity record the implementer emits.
`SKILL.md` names a variant; this file is the template. Substitute placeholders only.

## Emission

Queue packets (shape per `STANDARDS.md §8`) POST to `/api/v1/queue/entries`; activity records POST to `/api/v1/activity/entries`. Same headers for both:

```bash
curl -X POST "${HUB_BASE_URL:-https://sethgibson.com}/api/v1/<queue|activity>/entries" \
  -H "Content-Type: application/json" \
  -H "x-service-role-key: $QUEUE_SERVICE_ROLE_KEY" \
  -H "User-Agent: aethrix-fleet-ci/1.0 (implementer)" \
  -d '<packet JSON>'
```

Graceful failure (both kinds): missing key or non-2xx → log `::warning::`, continue per the SKILL.md flow (the exit still happens; only the write is skipped). Writes are observability, not gates.

## Common fields

Every packet includes:

```json
{
  "agent_name": "implementer",
  "product_slug": "<repo basename>"
}
```

## Queue-packet variants

| Variant | When (SKILL.md ref) | entry_type | risk_tier | request_id seed |
| --- | --- | --- | --- | --- |
| `ambiguity` | Step 2 Case B — criteria ambiguous/unworkable | strategic | medium | `sha256(repo + step-id + 'ambiguity')` |
| `human-action` | Step 2 Case C — step marked `*(human)*` | strategic | medium | `sha256(repo + step-id + 'human-action')` |
| `mid-step-split` | Step 2 Case D1 — after commit, pointing at new `*(human)*` sub-steps | strategic | medium | `sha256(repo + parent-step-id + 'mid-step-split')` |
| `mid-step-ambiguous-split` | Step 2 Case D2 — criteria entangled, no commit | strategic | medium | `sha256(repo + parent-step-id + 'mid-step-ambiguous-split')` |
| `adversary-cap-hit` | Step 4.5 — loop still `needs-fixes` at iteration 3 | exception | high | `sha256(run_id + 'adversary-cap-hit')` |

### `ambiguity` (Case B)

```json
{
  "request_id": "<sha256 of repo + step-id + 'ambiguity'>",
  "entry_type": "strategic",
  "risk_tier": "medium",
  "title": "[<slug>] PLANNING.md step <id> needs clarification",
  "goal": "Implement <step text>",
  "attempts": ["Read acceptance criteria; ambiguity: <specifics>"],
  "ask": "Clarify <specific question>",
  "recommendation": "<best-guess interpretation, optional>"
}
```

### `human-action` (Case C)

```json
{
  "request_id": "<sha256 of repo + step-id + 'human-action'>",
  "entry_type": "strategic",
  "risk_tier": "medium",
  "title": "[<slug>] PLANNING step <id> needs your manual action",
  "goal": "Advance PLANNING.md past step <id>",
  "attempts": ["Identified step <id> as next unblocked step; step is marked *(human)* per the PLANNING template instructions (templates/planning/INSTRUCTIONS.md, human-action marker)"],
  "ask": "<full step text including acceptance criteria, copied verbatim from PLANNING.md>",
  "recommendation": "Complete the manual action, then check off the step in PLANNING.md and commit. Implementer cannot advance past this step until that is done; downstream steps likely depend on it."
}
```

### `mid-step-split` (Case D1)

```json
{
  "request_id": "<sha256 of repo + parent-step-id + 'mid-step-split'>",
  "entry_type": "strategic",
  "risk_tier": "medium",
  "title": "[<slug>] PLANNING step <parent-id> split mid-implementation — <n> human sub-steps need your action",
  "goal": "Complete the human-only criteria split out from <parent-id>",
  "attempts": [
    "Implemented agent-doable scope of <parent-id> in PR #<n>",
    "Split human-only criteria into <list of new sub-step IDs> per the PLANNING template instructions (templates/planning/INSTRUCTIONS.md, human-action marker) mid-step discovery"
  ],
  "ask": "Complete the new *(human)* sub-steps (<list>); check them off in PLANNING.md. Next implementer run will block on these per Case C until they're done.",
  "recommendation": "Review the split in PR #<n>; if the partition is wrong, edit PLANNING.md to restore the parent step and reopen scope as needed before the next implementer run."
}
```

### `mid-step-ambiguous-split` (Case D2)

```json
{
  "request_id": "<sha256 of repo + parent-step-id + 'mid-step-ambiguous-split'>",
  "entry_type": "strategic",
  "risk_tier": "medium",
  "title": "[<slug>] PLANNING step <parent-id> mixes agent-doable and human-only work; how should I split it?",
  "goal": "Implement <parent step text>",
  "attempts": [
    "Identified human-only criteria via the PLANNING template's "When to mark" heuristics: <list>",
    "Could not cleanly partition — criteria appear entangled"
  ],
  "ask": "Restructure <parent-id> in PLANNING.md to separate agent-doable scope from *(human)* sub-steps; re-run implementer once the split is committed.",
  "recommendation": "<best-guess partition if you have one, else omit>"
}
```

### `adversary-cap-hit` (Step 5)

```json
{
  "request_id": "<sha256 of run_id + 'adversary-cap-hit'>",
  "entry_type": "exception",
  "risk_tier": "high",
  "run_id": "<run_id>",
  "title": "[<slug>] Adversary loop failed to converge on step <id>",
  "goal": "Implement <step text>",
  "attempts": ["Ran <n> adversary review iterations; final blocking findings: <count>"],
  "ask": "Adjudicate the unresolved blocking findings and the implementer's pushbacks; decide override / rework / abandon",
  "artifacts": [{ "artifact_type": "github-pr", "url": "<not-yet-created>" }],
  "recommendation": "Review the activity trail entries for run_id <run_id> for full iteration history"
}
```

### `workflow-failed` (central workflow, not the skill)

Emitted by `implementer-callable.yml`'s `if: failure()` handler (added 2026-06-04) when the claude-code-action step itself dies — max-turns, crash, infra error — meaning the agent never reached its own exit path, so no in-band packet or tick-report fired. Documented here for completeness; the skill never emits this variant. The handler also sends a tick-report with outcome `workflow-failed` (orchestrator maps it to `errored`).

```json
{
  "request_id": "<sha256 of github-run-id + 'workflow-failed'>",
  "entry_type": "exception",
  "risk_tier": "high",
  "title": "[<slug>] implementer workflow failed on <target_ref>",
  "goal": "Advance <target_kind>/<target_ref> by one step",
  "attempts": ["claude-code-action step failed before the skill's own exit path; no in-band queue entry or tick-report was sent. Run: <run_url>"],
  "ask": "Inspect the workflow run log; decide retry, fix the step/criteria, or restructure the target",
  "recommendation": "Check the last tool calls in the log — max-turns deaths usually mean the agent was attempting work outside its lane"
}
```

## Activity records

Both records share the queue-packet common fields plus `run_id`. POST to `/api/v1/a