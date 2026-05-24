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
- `.fleet-ci/.github/scripts/pre-commit-reviewer/SKILL.md` — Read this when ready to run the adversary review loop (Step 4.5 below).

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

**Scope the read.** On long-lived products `PLANNING.md` accumulates many milestones. Don't read top-to-bottom blindly — that wastes tokens on completed work the agent doesn't need to reason about. Read in two passes:

1. **The milestone-overview table** at the top of `PLANNING.md` (if present) — gives you the lay of the land in ~10 lines.
2. **The first milestone with unchecked steps** (the active milestone). Identify it by scanning `## M<n>` headings top-down and stopping at the first one that contains at least one `- [ ]` or `* [ ]` line below it.

Skip fully-completed milestones entirely — their content is preserved in git history; you don't need it to advance the next step. If `PLANNING.md` has been compacted (older milestones moved to `PLANNING_ARCHIVE.md` per the deferred `planning-compactor` convention), the file you read is already scoped; no behavior change needed.

**Find the next step.** Within the active milestone, find the first `- [ ]` or `* [ ]` line that is:

1. Inside a milestone section (`## M<n>` heading)
2. Not preceded by an explicit `**Blocked by:**` annotation referencing an unchecked dependency

The step text is everything between the checkbox and the next checkbox / heading / horizontal rule. Acceptance criteria are the indented bullets that follow.

If multiple steps look unblocked at the same indentation, take the first one (top-down order).

**Check for the `*(human)*` marker.** If the identified step's text begins with `*(human)*` immediately after the checkbox (per `STANDARDS.md §4.2`), this step is a hard blocker for the agent — Seth must complete it manually before the implementer can advance. Do NOT attempt to implement, do NOT skip ahead to the next step (downstream steps almost always depend on the human action), and do NOT guess at a workaround. Branch to Step 3's human-only case (Case C).

**Scan acceptance criteria for unmarked human-only work.** Even when the step itself lacks the `*(human)*` marker, the criteria below the checkbox may include sub-bullets that match `STANDARDS §4.2`'s "When to mark" criteria — interactive OAuth flows (`wrangler login`, `gh auth login`, `gcloud auth login`), local-machine verification (`verify ... runs locally`, "test on physical device," "open in browser"), third-party dashboard configuration ("create account at," "enable in Settings →"), payments, identity verification, App Store / external review waits, physical actions. If the criteria match any of these patterns and the step has no `*(human)*` marker, this is **mid-step discovery caught early** per `STANDARDS §4.2` "Mid-step discovery" subsection — branch to Step 3's Case D (split-and-continue or stop-and-ask) rather than walking into a partial-completion trap.

Treat this scan as a heuristic, not a rule engine. The goal is to catch obvious cases at the earliest point; the Case D flow at Step 3 still catches anything the scan misses.

### Step 2 — Read context

Read these before implementing:

- The step's acceptance criteria (just identified)
- `/docs/PRD.md` — only the section(s) the step relates to
- Any files the step's acceptance criteria explicitly mention
- Recent commits via `git log --oneline -20` to see what's been built lately
- `/docs/DECISIONS.md` if it exists — for stack/architectural context

Do NOT read the whole repo. Use the acceptance criteria to scope what's relevant.

### Step 3 — Decide what to do (implement, escalate, or human-only exit)

**Case A — Normal step (no `*(human)*` marker, criteria are workable).** Make the code changes the acceptance criteria call for. Apply normal engineering judgment: small focused commits-worth of work, prefer extending existing patterns to inventing new ones, follow the conventions in `/docs/CLAUDE.md` and existing similar code.

If the step requires a decision that's outside Seth's creative & strategic lane (per `STANDARDS.md §10`) and the acceptance criteria don't constrain the choice, make a reasonable call and record it in `/docs/DECISIONS.md` with a one-line rationale. The commit skill's tier classification will route to the queue if the decision is high-risk.

**Case B — Acceptance criteria ambiguous or unworkable as written.** Do NOT guess. Emit a `strategic` queue entry via direct POST to `${HUB_BASE_URL:-https://sethgibson.com}/api/v1/queue/entries` describing the ambiguity, then exit (no commit). Packet shape per `STANDARDS.md §8`:

```json
{
  "request_id": "<sha256 of repo + planning-step-id + 'ambiguity'>",
  "entry_type": "strategic",
  "risk_tier": "medium",
  "agent_name": "implementer",
  "product_slug": "<repo basename, if available>",
  "title": "[<slug>] PLANNING.md step <id> needs clarification",
  "goal": "Implement <step text>",
  "attempts": ["Read acceptance criteria; ambiguity: <specifics>"],
  "ask": "Clarify <specific question>",
  "recommendation": "<best-guess interpretation, optional>"
}
```

**Case C — Step is marked `*(human)*` (detected in Step 1).** The step requires Seth's manual action; the agent cannot advance. Do NOT implement, do NOT skip to the next step. Emit a `strategic` queue entry per `STANDARDS.md §4.2` + §8, then exit cleanly (no commit). Packet shape:

```json
{
  "request_id": "<sha256 of repo + planning-step-id + 'human-action'>",
  "entry_type": "strategic",
  "risk_tier": "medium",
  "agent_name": "implementer",
  "product_slug": "<repo basename, if available>",
  "title": "[<slug>] PLANNING step <id> needs your manual action",
  "goal": "Advance PLANNING.md past step <id>",
  "attempts": ["Identified step <id> as next unblocked step; step is marked *(human)* per STANDARDS §4.2"],
  "ask": "<full step text including acceptance criteria, copied verbatim from PLANNING.md>",
  "recommendation": "Complete the manual action, then check off the step in PLANNING.md and commit. Implementer cannot advance past this step until that is done; downstream steps likely depend on it."
}
```

Log a final line and exit:

```
implementer: step <id> requires human action per STANDARDS §4.2; emitted strategic queue entry <id>. Exiting.
```

Skip Steps 4–6.

**Case D — Mid-step discovery of unmarked human-only work (per `STANDARDS §4.2` "Mid-step discovery").** Triggers when either (a) Step 1's heuristic scan flagged this step upfront, OR (b) during implementation you realize part of the acceptance criteria matches the §4.2 "When to mark" criteria (interactive OAuth, local-machine verification, third-party dashboard config, payments, external review waits, physical actions). Critical rule: **do NOT mark the parent step `[x]` based on partial work** — PLANNING.md is the only durable surface for "what's next," and misrepresenting completion there causes downstream runs to fail far from the cause.

Two sub-cases:

**Case D1 — Clean split (default).** Use this when you can cleanly identify which acceptance criteria bullets are agent-doable vs. human-only.

1. Do the agent-doable work normally (per Case A's engineering judgment).
2. Edit `PLANNING.md` inline in this same PR (per the inline-doc-update convention resolved 2026-05-20):
   - Append a parenthetical to the parent step's text: `(split mid-implementation per STANDARDS §4.2; criteria <list> moved to M<n>.<x>a/b/...)`.
   - Insert new `*(human)*` sub-steps immediately after the parent step, named `M<n>.<x>a`, `M<n>.<x>b`, etc. Each sub-step's text restates the human-only criterion as a discrete action.
   - Mark the parent step `[x]` — its remaining (agent-doable) scope is complete.
3. Continue to Step 4 (tests) and Step 4.5 (adversary loop) normally.
4. After invoking `commit` (Step 5), emit an additional `strategic` queue entry pointing at the new sub-steps. This is in addition to whatever queue/auto-merge action `commit` takes for the PR itself — different ask, different recipient mental model:

```json
{
  "request_id": "<sha256 of repo + parent-step-id + 'mid-step-split'>",
  "entry_type": "strategic",
  "risk_tier": "medium",
  "agent_name": "implementer",
  "product_slug": "<repo basename, if available>",
  "title": "[<slug>] PLANNING step <parent-id> split mid-implementation — <n> human sub-steps need your action",
  "goal": "Complete the human-only criteria split out from <parent-id>",
  "attempts": ["Implemented agent-doable scope of <parent-id> in PR #<n>", "Split human-only criteria into <list of new sub-step IDs> per STANDARDS §4.2 mid-step discovery"],
  "ask": "Complete the new *(human)* sub-steps (<list>); check them off in PLANNING.md. Next implementer run will block on these per Case C until they're done.",
  "recommendation": "Review the split in PR #<n>; if the partition is wrong, edit PLANNING.md to restore the parent step and reopen scope as needed before the next implementer run."
}
```

**Case D2 — Ambiguous split (fallback).** Use this when you cannot cleanly partition the criteria — they're entangled, or you can't tell what's agent-doable without guessing. Do NOT commit anything. Same shape as Case B:

```json
{
  "request_id": "<sha256 of repo + parent-step-id + 'mid-step-ambiguous-split'>",
  "entry_type": "strategic",
  "risk_tier": "medium",
  "agent_name": "implementer",
  "product_slug": "<repo basename, if available>",
  "title": "[<slug>] PLANNING step <parent-id> mixes agent-doable and human-only work; how should I split it?",
  "goal": "Implement <parent step text>",
  "attempts": ["Identified human-only criteria via STANDARDS §4.2 heuristics: <list>", "Could not cleanly partition — criteria appear entangled"],
  "ask": "Restructure <parent-id> in PLANNING.md to separate agent-doable scope from *(human)* sub-steps; re-run implementer once the split is committed.",
  "recommendation": "<best-guess partition if you have one, else omit>"
}
```

Log a final line and exit:

```
implementer: step <parent-id> needs split per STANDARDS §4.2; emitted strategic queue entry <id>. Exiting without commit.
```

Skip Steps 4–6.

**Choosing between D1 and D2.** Default to D1. Fall back to D2 only when the partition is genuinely unclear — entangled phrasing, criteria that depend on each other in ways that can't be cleanly separated, or doubt about whether a criterion is human-only. When in doubt, prefer D2; it's better to stop and ask than to restructure PLANNING.md the wrong way and force Seth to unwind it.

### Step 4 — Run tests

If the repo has a test runner (`package.json` `scripts.test`, `pytest.ini`, etc.), run it. If tests fail and the failure is in code this step touched, fix it before continuing. If tests fail in unrelated code, note the failure in the PR body's `## Notes` section and continue — the commit skill's CI-red handling will queue it appropriately.

If no test infrastructure exists, skip this step. Don't create test files speculatively — that's the `test-writer` skill's job, invoked separately.

### Step 4.5 — Run the adversary review loop

Orchestrate the `pre-commit-reviewer` skill in an iterative loop. The reviewer is **stateless per invocation**; you (the implementer) own the loop state and the convergence decision.

Read `.fleet-ci/.github/scripts/pre-commit-reviewer/SKILL.md` once at the start to understand its contract. Each iteration is one fresh review pass — feed it the current diff plus, on iterations ≥ 2, any pushback rationales from the prior iteration.

**Generate the `run_id` once at run start** (e.g., `sha256(repo + step_id + git rev-parse HEAD)`); reuse it across all iterations and the implementer's own activity records.

**Loop, max 3 iterations:**

1. Invoke the reviewer per its SKILL.md — pass `run_id`, `iteration_number`, `step_id`, and (if iteration ≥ 2) `prior_findings` + `implementer_pushbacks`.
2. Parse the reviewer's JSON output (`schema_version: 1`).
3. Branch on `outcome`:
   - **`clean`** → exit loop. Continue to Step 5 (commit).
   - **`signoff-with-caveats`** → exit loop. Carry the caveats forward to Step 5; they land in the PR body's `## Adversary review` section and on the activity record. Continue to commit.
   - **`needs-fixes`** → for each blocking finding, decide:
     - **Fix in place** — make the code change the finding calls for. Default action.
     - **Push back** — record a `{ finding_id, rationale }` entry. Push back only when you have a concrete reason (e.g., "this is intentionally out of scope per PRD §3.2," not "this seems fine"). Pushbacks without concrete rationale are wastes of the iteration budget.
   - If you fixed at least one thing, increment `iteration_number` and re-invoke (loop iterates).
   - If you pushed back on everything without fixing, increment and re-invoke with the pushbacks — the reviewer adjudicates.
4. **Cap-hit:** if `iteration_number` reaches 3 and `outcome` is still `needs-fixes`, exit the loop and emit an `exception` queue entry per `STANDARDS.md §8`:

```json
{
  "request_id": "<sha256 of run_id + 'adversary-cap-hit'>",
  "entry_type": "exception",
  "risk_tier": "high",
  "agent_name": "implementer",
  "product_slug": "<repo basename>",
  "run_id": "<run_id>",
  "title": "[<slug>] Adversary loop failed to converge on step <id>",
  "goal": "Implement <step text>",
  "attempts": ["Ran <n> adversary review iterations; final blocking findings: <count>"],
  "ask": "Adjudicate the unresolved blocking findings and the implementer's pushbacks; decide override / rework / abandon",
  "artifacts": [{ "artifact_type": "github-pr", "url": "<not-yet-created>" }],
  "recommendation": "Review the activity trail entries for run_id <run_id> for full iteration history"
}
```

Then exit (no commit). Do NOT push code to a PR when the adversary loop cap-hits — the unresolved blocking findings are the point of the gate.

**Activity-trail writes by this step:** the reviewer writes one record per iteration on its own (per `pre-commit-reviewer/SKILL.md`). The implementer doesn't double-write the review records, but does record loop-bracketing events as part of its own activity-trail writes (see "Activity trail writes" near the end of this skill).

### Step 5 — Invoke the `commit` skill

Read `.fleet-ci/.github/scripts/commit/SKILL.md` and follow its 8-step flow. The commit skill handles:

- Tier classification (must-escalate parse + matrix + elevations)
- Inline canonical-doc updates at Step 4b (`PLANNING.md`, `LIFECYCLE.md`, `CHANGELOG.md`, `PRODUCT.md`) — folded into the same commit as the code change. No separate post-merge cleanup phase.
- Branch / commit / push / open PR with the right body composition + labels
- Auto-merge (pre-prod) or queue for approval (post-prod) per the (tier × stage) gating table

The implementer does NOT pre-update those docs itself — the commit skill owns Step 4b. Pass the step ID through so commit can flip the right PLANNING checkbox.

**Pass the step ID to the commit skill** so the PR body's `## PLANNING.md step` section is populated correctly. Concretely: in the PR body's Summary, include "Closes step M<n>.<x>" so the commit skill's PR-body composer can pick it up.

**Pass the implementer's `run_id` to the commit skill** (export as `IMPLEMENTER_RUN_ID` env var before invoking) so commit's four mid-flight activity records stitch into the same logical run as the implementer's `run-started` / `run-completed` bracket. Commit's records are required mid-flight observability per `STANDARDS §9` "Commit-skill exit contract" — when the implementer dies inside Step 5 (max-turns, crash), commit's `commit-started` / `tier-classified` / `pr-opened` / `commit-exited` records localize the failure to a phase boundary.

### Step 6 — Exit

When the commit skill returns its terminal `exit_reason` (`auto_merge_initiated`, `queued`, `exception_emitted`, or `fallback_in_chat`), this skill is done. **Do NOT loop into the next step. Do NOT wait for the PR to actually merge, for CI to go green, or for staging deploy to succeed.** Those are all out-of-process per `STANDARDS §9` "Commit-skill exit contract" — GitHub's native auto-merge, branch protection, CD, and the merge fire-back loop (`§11.2`) own them. The verify gate is the loop boundary.

Forbidden in this step (and anywhere in the implementer):

- `gh pr view --json state,mergedAt` polling loops.
- `gh pr checks <PR>` re-reads after Step 4e captured `<CI_STATUS>`.
- `sleep` + retry on any merge-or-deploy signal.

Each of those calls is one SDK turn. Polling for events that aren't this agent's responsibility is what burned puzzle-pop M1.7's 80-turn budget (2026-05-24) — see `STANDARDS §9`.

Log a final line:

```
implementer: step M<n>.<x> complete; PR <url>; gate=<auto-merge|queue|exception|fallback>. Exiting.
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

## Activity trail writes

The implementer brackets its own run with two activity records per `STANDARDS.md` Agent Activity Trail section. The reviewer (Step 4.5) writes its own per-iteration records; this skill does NOT double-write those.

**Run-start record** — written immediately after Step 1 identifies a workable next step (after Case A/B/C branching is decided, before Step 2 reads context):

```bash
curl -X POST "${HUB_BASE_URL:-https://sethgibson.com}/api/v1/activity/entries" \
  -H "Content-Type: application/json" \
  -H "x-service-role-key: $QUEUE_SERVICE_ROLE_KEY" \
  -H "User-Agent: aethrix-fleet-ci/1.0 (implementer)" \
  -d '{
    "request_id": "<sha256 of run_id + \"run-started\">",
    "product_slug": "<repo basename>",
    "agent_name": "implementer",
    "action": "run-started",
    "run_id": "<run_id>",
    "payload": {
      "step_id": "<M2.7>",
      "step_text": "<full step text>",
      "case": "A" | "B" | "C",
      "github_run_id": "$GITHUB_RUN_ID",
      "github_sha": "$GITHUB_SHA"
    }
  }'
```

**Run-completed record** — written at exit, regardless of outcome:

```json
{
  "request_id": "<sha256 of run_id + 'run-completed'>",
  "product_slug": "<repo basename>",
  "agent_name": "implementer",
  "action": "run-completed",
  "run_id": "<run_id>",
  "payload": {
    "step_id": "<M2.7>",
    "outcome": "committed" | "queued-for-approval" | "blocked-strategic" | "blocked-human" | "blocked-adversary-cap-hit" | "blocked-tests" | "guard-tripped",
    "iterations_used": <0-3>,
    "pr_url": "<url or null>",
    "queue_entry_id": "<id if any queue entry was emitted, else null>"
  }
}
```

Write the run-completed record on EVERY exit path — Cases B/C exits, cap-hit exits, guard trips, successful commits. The Agent Activity Trail UI relies on a paired start/complete to render run duration correctly.

Graceful failure on activity writes follows the same pattern as `pre-commit-reviewer/SKILL.md`: missing key or non-2xx → log `::warning::`, continue. Activity writes are observability, not gates.

---

## Failure modes

| Failure | Behavior |
| --- | --- |
| Guard 1–3 trip | Exit cleanly with `::warning::` or `::notice::` log line. No queue entry, no activity record (run hadn't started). |
| Guard 4 trips (when implemented) | Same as above. |
| Next step is marked `*(human)*` (`STANDARDS §4.2`) | Emit `strategic` queue entry per Step 3 Case C; write `run-completed` record with `outcome: "blocked-human"`; exit cleanly. No commit, no attempt to skip ahead. |
| Step implementation fails (cannot determine what to do) | Emit `strategic` queue entry per Step 3 Case B; write `run-completed` with `outcome: "blocked-strategic"`; exit. |
| Tests fail in code this step touched and agent can't fix | Continue to commit; note in PR body `## Notes`. The commit skill's CI-red handling flips would-be-auto-merge to MEDIUM queue. `run-completed` outcome is `"queued-for-approval"` or `"committed"` depending on commit gate. |
| Tests fail in unrelated code | Note in `## Notes`; continue. Same CI-red flow if CI catches it. |
| Adversary loop returns `clean` or `signoff-with-caveats` | Continue to Step 5 (commit). |
| Adversary loop cap-hits (`needs-fixes` on iteration 3) | Emit `exception` queue entry per Step 4.5; write `run-completed` with `outcome: "blocked-adversary-cap-hit"`; exit. No commit. |
| Reviewer SKILL.md missing from `.fleet-ci/` | Log `::warning::implementer: pre-commit-reviewer SKILL.md missing; skipping adversary loop` and proceed to Step 5. This is a CI configuration failure; don't gate work on it. Operator notices via the missing review records in the activity trail. |
| Commit skill exits cleanly (auto-merged or queued) | Implementer exits success; `run-completed` outcome is `"committed"` or `"queued-for-approval"`. |
| Commit skill emits exception entry | Implementer exits success — the exception entry is the right surface for the failure, not a duplicate workflow failure. `run-completed` outcome is `"queued-for-approval"`. |
| `claude-code-action` itself errors | The workflow's error handling fires; if a queue entry for that case is wanted, the central workflow's `if: failure()` path emits one (like security-review's pattern). `run-completed` may not write — workflow-level failure, not skill-level. |
| `QUEUE_SERVICE_ROLE_KEY` missing | Commit skill's graceful fallback fires; reviewer and implementer activity writes both skip with `::warning::`. The skill itself doesn't fail; the operator notices via missing queue entry and missing activity records. |

---

## Concurrency

The agent does NOT enforce concurrency. The central workflow does, via `concurrency: { group: implementer-${{ github.repository }}, cancel-in-progress: false }`. This serializes implementer runs per-product — a queued second invocation waits for the first to finish rather than racing.

---

## What this skill does NOT do

- **Multiple PLANNING steps per run.** One step per invocation; exits after commit. The verify gate is the loop boundary.
- **Skip past `*(human)*`-marked steps.** Those are hard blockers per `STANDARDS §4.2`. Emit `strategic` queue entry (Step 3 Case C) and exit; do not advance to the next unchecked step. If Seth wants out-of-order execution, he reorders PLANNING.
- **Decide tech-stack or major architecture.** Those are HIGH tier per `§9` elevation rules and always queue. If a step's acceptance criteria require such a decision, emit a `strategic` queue entry and exit (per Step 3 Case B ambiguity flow).
- **Modify PLANNING.md itself beyond what's needed for the completed step.** The commit skill's Step 4b owns inline doc updates (flipping the completed checkbox, refreshing LIFECYCLE/CHANGELOG/PRODUCT). Restructuring or re-prioritizing PLANNING is Seth's lane.
- **Make creative or strategic calls.** Per `STANDARDS.md §10` — those queue as `strategic` entries. The agent prepares; Seth decides.
- **Wait for queue entry resolution.** The skill exits after commit; the wake mechanism (when built) is what resumes the loop.
- **Self-debug claude-code-action infrastructure issues.** Token expiry, quota exhaustion, etc. surface as workflow failures; the operator (or a future ops agent) handles them.
