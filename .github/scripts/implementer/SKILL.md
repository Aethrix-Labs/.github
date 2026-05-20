---
name: implementer
description: "Use this skill when an agent (typically the central implementer workflow) needs to advance a product's PLANNING.md by one step — read PLANNING, pick the next unblocked unchecked step, implement it, run tests, and invoke the `commit` skill to wrap up. Always exits after one step; the verify gate is the loop boundary. Triggers in CI on `workflow_dispatch` (manual or hub-mediated wake) and is not typically invoked on-demand by Seth — for one-off code work, just use Claude Code directly."
---

# Implementer Skill

The agent that does one PLANNING.md step at a time, autonomously. Lives in CI; invoked by the central reusable workflow `Aethrix-Labs/.github:.github/workflows/implementer-callable.yml`, which is called from a per-product stub at `<product-repo>:.github/workflows/implementer.yml`.

**Loop boundary is intentional.** This skill does ONE step then exits. To advance, another invocation is needed — manually from the GitHub Actions UI (MVP), or automatically via the wake mechanism when Seth green-lights a verify entry from the hub queue (full loop, deferred).

Spec source: `STANDARDS.md §9` per-step verification gate. The implementer is the wake target.

---

## Inputs

From the workflow's dispatch payload:

- `action` — `"next-step"` (default) or `"address-feedback"` (deferred — see below).
- `verify_entry_id` — optional. The hub queue entry ID this run is responding to. Only set when invoked by the wake mechanism; absent on manual triggers.
- `product_slug` — repo name (defaults to `$GITHUB_REPOSITORY` basename).

From the workflow environment:

- `$GITHUB_REPOSITORY`, `$GITHUB_RUN_ID`, `$GITHUB_SERVER_URL`, `$GITHUB_SHA` — standard.
- `$QUEUE_SERVICE_ROLE_KEY` — for the commit skill's queue emission path. Required.
- `$CLAUDE_CODE_OAUTH_TOKEN` — used by `claude-code-action` for auth; not consumed directly by this skill.

From the consumer repo:

- `/docs/PLANNING.md` (or `/PLANNING.md` at root) — source of truth for what to do next.
- `/docs/CLAUDE.md` — per-product instructions; honored throughout.
- `/docs/LIFECYCLE.md` — read for `stage:` and `monetized:` (commit skill needs these too).

From the central checkout at `.fleet-ci/`:

- `.fleet-ci/.github/scripts/commit/SKILL.md` — Read this when ready to commit; follow its 8-step flow.

---

## Pre-flight guards

Run in order. Any guard tripping → exit cleanly (workflow neutral) with a log line; do NOT emit a queue entry for guard failures (they're operational, not engineering failures).

**Guard 1 — Working tree clean.**

```bash
git status --porcelain
```

Output non-empty → there's uncommitted work in the repo. Exit with `::warning::implementer: working tree not clean; refusing to run`. Should never happen on a fresh checkout but worth checking in case the workflow is invoked on a non-default ref.

**Guard 2 — PLANNING.md exists.**

Check `/docs/PLANNING.md`, then `/PLANNING.md`. Missing both → exit with `::warning::implementer: no PLANNING.md found; nothing to do`.

**Guard 3 — At least one unchecked step exists.**

Grep PLANNING.md for `- [ ]` or `* [ ]`. Zero matches → exit with `::notice::implementer: all PLANNING.md steps complete`. This is the natural end-state for the product's current milestone; no error, just done.

**Guard 4 — No unresolved verify entries on this product** *(deferred until hub GET endpoint exists).*

When the hub exposes `GET /api/v1/queue/entries?product=<slug>&status=open&entry_type=verify`, this guard reads it. Any unresolved verify entry → exit with `::warning::implementer: unresolved verify entry blocks advance; resolve at <hub-url> first`. For MVP: skip this guard. Operator discipline (Seth doesn't click "Run workflow" until staging verified) substitutes.

---

## The flow

### Step 1 — Identify the next step

Read PLANNING.md from top to bottom. Find the first `- [ ]` or `* [ ]` line that is:

1. Inside a milestone section (`## M<n>` heading)
2. Not preceded by an explicit `**Blocked by:**` annotation referencing an unchecked dependency

The step text is everything between the checkbox and the next checkbox / heading / horizontal rule. Acceptance criteria are the indented bullets that follow.

If multiple steps look unblocked at the same indentation, take the first one (top-down order).

### Step 2 — Read context

Read these before implementing:

- The step's acceptance criteria (just identified)
- `/docs/PRD.md` — only the section(s) the step relates to
- Any files the step's acceptance criteria explicitly mention
- Recent commits via `git log --oneline -20` to see what's been built lately
- `/docs/DECISIONS.md` if it exists — for stack/architectural context

Do NOT read the whole repo. Use the acceptance criteria to scope what's relevant.

### Step 3 — Implement the step

Make the code changes the acceptance criteria call for. Apply normal engineering judgment: small focused commits-worth of work, prefer extending existing patterns to inventing new ones, follow the conventions in `/docs/CLAUDE.md` and existing similar code.

If the step requires a decision that's outside Seth's creative & strategic lane (per `STANDARDS.md §10`) and the acceptance criteria don't constrain the choice, make a reasonable call and record it in `/docs/DECISIONS.md` with a one-line rationale. The commit skill's tier classification will route to the queue if the decision is high-risk.

If the step's acceptance criteria are ambiguous or unworkable as written: do NOT guess. Emit a `strategic` queue entry via direct POST to `${HUB_BASE_URL:-https://sethgibson.com}/api/v1/queue/entries` describing the ambiguity, then exit (no commit). Packet shape per `STANDARDS.md §8`:

```json
{
  "request_id": "<sha256 of repo + planning-step-id + 'ambiguity'>",
  "entry_type": "strategic",
  "risk_tier": "medium",
  "agent_name": "implementer",
  "title": "[<slug>] PLANNING.md step <id> needs clarification",
  "goal": "Implement <step text>",
  "attempts": ["Read acceptance criteria; ambiguity: <specifics>"],
  "ask": "Clarify <specific question>",
  "recommendation": "<best-guess interpretation, optional>"
}
```

### Step 4 — Run tests

If the repo has a test runner (`package.json` `scripts.test`, `pytest.ini`, etc.), run it. If tests fail and the failure is in code this step touched, fix it before continuing. If tests fail in unrelated code, note the failure in the PR body's `## Notes` section and continue — the commit skill's CI-red handling will queue it appropriately.

If no test infrastructure exists, skip this step. Don't create test files speculatively — that's the `test-writer` skill's job, invoked separately.

### Step 5 — Invoke the `commit` skill

Read `.fleet-ci/.github/scripts/commit/SKILL.md` and follow its 8-step flow. The commit skill handles:

- Tier classification (must-escalate parse + matrix + elevations)
- Branch / commit / push / open PR with the right body composition + labels
- Auto-merge (pre-prod) or queue for approval (post-prod) per the (tier × stage) gating table
- Post-merge cleanup (the merge fire-back loop handles this asynchronously when it ships; until then, the auto-merge path runs cleanup inline)

**Pass the step ID to the commit skill** so the PR body's `## PLANNING.md step` section is populated correctly. Concretely: in the PR body's Summary, include "Closes step M<n>.<x>" so the commit skill's PR-body composer can pick it up.

### Step 6 — Exit

After commit fires (whether it auto-merged or queued for approval), this skill is done. **Do NOT loop into the next step.** The verify gate is the loop boundary.

Log a final line:

```
implementer: step M<n>.<x> complete; PR <url>; gate=<auto-merge|queue>. Exiting.
```

---

## The `action: "address-feedback"` flow (DEFERRED)

When Seth selects "feedback" on a verify queue entry (per `STANDARDS.md §9` resolution paths), the wake mechanism dispatches this workflow with `action: "address-feedback"` and `verify_entry_id`.

The implementer skill should:

1. GET the verify entry from the hub to read the feedback payload — REQUIRES hub queue read endpoint (not yet implemented; tracked in `STANDARDS.md §11.1`).
2. The original PR was already merged; open a NEW PR addressing the feedback.
3. Same Steps 2–6 as the next-step flow.
4. On commit success, the new PR will fire a fresh verify entry when it merges + deploys to staging.

For MVP: this flow is **not implemented**. If invoked with `action: "address-feedback"`, log `::warning::implementer: address-feedback action not yet implemented (deferred — needs hub GET endpoint); exiting` and exit cleanly. Seth manually addresses feedback in Claude Code or a fresh Cowork session in the product project.

---

## Failure modes

| Failure | Behavior |
| --- | --- |
| Guard 1–3 trip | Exit cleanly with `::warning::` or `::notice::` log line. No queue entry. |
| Guard 4 trips (when implemented) | Same as above. |
| Step implementation fails (cannot determine what to do) | Emit `strategic` queue entry per Step 3 ambiguity flow; exit cleanly. |
| Tests fail in code this step touched and agent can't fix | Continue to commit; note in PR body `## Notes`. The commit skill's CI-red handling flips would-be-auto-merge to MEDIUM queue. |
| Tests fail in unrelated code | Note in `## Notes`; continue. Same CI-red flow if CI catches it. |
| Commit skill exits cleanly (auto-merged or queued) | Implementer exits success. |
| Commit skill emits exception entry | Implementer exits success — the exception entry is the right surface for the failure, not a duplicate workflow failure. |
| `claude-code-action` itself errors | The workflow's error handling fires; if a queue entry for that case is wanted, the central workflow's `if: failure()` path emits one (like security-review's pattern). |
| `QUEUE_SERVICE_ROLE_KEY` missing | Commit skill's graceful fallback fires (in-chat approval message — visible only in workflow logs, not actionable in CI). The implementer skill itself doesn't fail; the operator notices via the missing queue entry. |

---

## Concurrency

The agent does NOT enforce concurrency. The central workflow does, via `concurrency: { group: implementer-${{ github.repository }}, cancel-in-progress: false }`. This serializes implementer runs per-product — a queued second invocation waits for the first to finish rather than racing.

---

## What this skill does NOT do

- **Multiple PLANNING steps per run.** One step per invocation; exits after commit. The verify gate is the loop boundary.
- **Decide tech-stack or major architecture.** Those are HIGH tier per `§9` elevation rules and always queue. If a step's acceptance criteria require such a decision, emit a `strategic` queue entry and exit (per Step 3 ambiguity flow).
- **Modify PLANNING.md itself beyond checking off the completed step.** That happens in the commit skill's post-merge cleanup (Step 6a). Restructuring or re-prioritizing PLANNING is Seth's lane.
- **Make creative or strategic calls.** Per `STANDARDS.md §10` — those queue as `strategic` entries. The agent prepares; Seth decides.
- **Wait for queue entry resolution.** The skill exits after commit; the wake mechanism (when built) is what resumes the loop.
- **Self-debug claude-code-action infrastructure issues.** Token expiry, quota exhaustion, etc. surface as workflow failures; the operator (or a future ops agent) handles them.
