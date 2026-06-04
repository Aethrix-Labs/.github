---
name: implementer
description: "Use this skill when an agent (the central implementer workflow) needs to advance a product's work by one step — resolve the hub-directed target (milestone or backlog item), implement the next unblocked step, run tests, and invoke the `commit` skill to wrap up. Always exits after one step; the verify gate is the loop boundary. Triggers in CI on `workflow_dispatch` from the hub's autonomous-dev loop only — manual dispatch is unsupported. Not invoked on-demand by Seth; for one-off code work, just use Claude Code directly."
canonical_source: fleet-command-center/skills/implementer/SKILL.md
---

# Implementer Skill

The agent that does one PLANNING.md step at a time, autonomously. Lives in CI; invoked by the central reusable workflow `Aethrix-Labs/.github:.github/workflows/implementer-callable.yml`, which is called from a per-product stub at `<product-repo>:.github/workflows/implementer.yml`.

**Loop boundary is intentional.** This skill does ONE step then exits. Advancing requires another dispatch from the hub's orchestrator (the play-button loop re-dispatches the same sticky target until the milestone cap or a gate).

Spec source: `STANDARDS.md §9` per-step verification gate.

---

## Inputs

From the workflow's dispatch payload:

- `product_slug` — repo name (defaults to `$GITHUB_REPOSITORY` basename).
- `session_id` — The autonomous session ID from the hub play-button loop. When set, a tick-report POST is sent at exit (see Step 7).
- `hub_url` — Hub base URL for tick-report POST (default: `https://sethgibson.com`). Set by the hub's `dispatchImplementer` call.
- `target_kind` — **Required.** The hub picker's directed target kind: `"backlog"` or `"milestone"`. Read from `$IMPLEMENTER_TARGET_KIND`. Empty → Guard 2 trips (manual dispatch is unsupported).
- `target_ref` — **Required.** The directed target's ref: `BL-<n>` for a backlog item, `M<n>` for a milestone. Read from `$IMPLEMENTER_TARGET_REF`.

From the workflow environment:

- `$GITHUB_REPOSITORY`, `$GITHUB_RUN_ID`, `$GITHUB_SERVER_URL`, `$GITHUB_SHA` — standard.
- `$QUEUE_SERVICE_ROLE_KEY` — for this skill's queue packets + activity records and the commit skill's queue emission. Required.
- `$AUTONOMOUS_TICK_TOKEN` — for the tick-report POST. Required when `session_id` is set; otherwise not consumed.
- `$CLAUDE_CODE_OAUTH_TOKEN` — used by `claude-code-action` for auth; not consumed directly by this skill.

From the consumer repo:

- `/docs/PLANNING.md` (or `/PLANNING.md` at root) — source of truth for what to do next.
- `/docs/CLAUDE.md` — per-product instructions; honored throughout.
- `/docs/LIFECYCLE.md` — read for `stage:` and `monetized:` (commit skill needs these too).

From the central checkout at `.fleet-ci/`:

- `.fleet-ci/.github/scripts/commit/SKILL.md` — Read this when ready to commit; follow its flow.
- `.fleet-ci/.github/scripts/pre-commit-reviewer/SKILL.md` — Read this when ready to run the adversary review loop (Step 5 below).
- `.fleet-ci/.github/scripts/implementer/PACKETS.md` — queue-packet and activity-record templates. Read it before the first emission (the `run-started` activity record fires on every run that passes Step 1); SKILL.md names variants, PACKETS.md holds the JSON.

---

## Pre-flight guards

Run in order. Any guard tripping → exit cleanly (workflow neutral) with a log line; do NOT emit a queue entry for guard failures (they're operational, not engineering failures).

**Guard 1 — Working tree clean.**

```bash
git status --porcelain
```

Output non-empty → there's uncommitted work in the repo. Exit with `::warning::implementer: working tree not clean; refusing to run`. Should never happen on a fresh checkout but worth checking in case the workflow is invoked on a non-default ref.

**Guard 2 — Directed target present.**

`$IMPLEMENTER_TARGET_KIND` or `$IMPLEMENTER_TARGET_REF` empty → exit with `::warning::implementer: no directed target; manual dispatch is unsupported — start runs from the hub`. The hub's start route always sends both; an empty target means a manual Actions-UI trigger or a stale caller.

On any guard trip, send tick-report at exit if `session_id` is set (see Step 7).

---

## The flow

### Step 1 — Identify the next work item

The hub has bound this session to a specific target via `$IMPLEMENTER_TARGET_KIND` / `$IMPLEMENTER_TARGET_REF` (Guard 2 already verified both are set). The target selects both the surface and the item directly.

**Generate the `run_id` now** (e.g., `sha256(repo + target_ref + git rev-parse HEAD)`); reuse it across the activity records, the adversary loop iterations, and the commit handoff.

**`target_kind == "milestone"`** (`target_ref` is `M<n>`):

1. Locate the `## <target_ref>` heading in `/docs/PLANNING.md` (or `/PLANNING.md`). If the milestone heading doesn't exist (renamed / removed since the picker enumerated it), treat as "no work" — log `::notice::implementer: directed milestone <target_ref> not found; nothing to do` and exit cleanly with tick-outcome `guard-tripped` (the orchestrator's target-scoped check will mark the session `done`). A directed dispatch must never silently pick something else.
2. Within that milestone, find the **first** `- [ ]` / `* [ ]` step that is (a) not preceded by an unresolved `**Blocked by:**` annotation and (b) does not begin with `*(human)*`. That step is the work item. Its text is everything between the checkbox and the next checkbox / heading / horizontal rule; acceptance criteria are the indented bullets that follow.
3. If the milestone has **no** qualifying step (all done, all blocked, or all `*(human)*`), log `::notice::implementer: directed milestone <target_ref> has no unblocked step; nothing to do` and exit cleanly with tick-outcome `guard-tripped`. (The picker only offers milestones with ≥1 unblocked step and the start route 422s stale targets, but a step can be consumed between enumeration and dispatch — handle it defensively, never by diverting to another target.)
4. **Scan the acceptance criteria for unmarked human-only work** — sub-bullets matching `STANDARDS §4.2`'s "When to mark" criteria (the pattern list in Step 3 Case D). A match → branch to Step 3 Case D rather than walking into a partial-completion trap. Treat the scan as a heuristic; Case D's mid-implementation trigger still catches anything it misses. Then continue to **Step 2**.

**`target_kind == "backlog"`** (`target_ref` is `BL-<n>`):

1. In `/docs/BACKLOG.md` `## Open`, find the `- [ ]` item whose ID is `<target_ref>`. If it's gone (already closed / not found), log `::notice::implementer: directed backlog item <target_ref> not found in ## Open; nothing to do` and exit cleanly with tick-outcome `guard-tripped`.
2. If the item's text begins with `*(human)*` → branch to Step 3 Case C with `<target_ref>` substituted for the step ID. Otherwise the **item body is the acceptance criteria**: the indented bullets / paragraphs / embedded screenshots beneath the `- [ ]` line, up to the next `- [ ]` or `##` heading. The `BL-<n>` ID takes the place of the PLANNING step ID in all downstream Step 3 / Step 6 / activity-trail references. Continue to **Step 2**.
3. **Elevation check (applies throughout implementation).** Backlog items are tier-low by convention (per `STANDARDS §4.3`). If you discover the item is actually tier-medium-or-higher (needs a feature flag, schema migration, multi-step coordination, public-API change), do NOT silently widen scope — emit the **`ambiguity`** packet per `PACKETS.md` with `ask: "Backlog item <BL-id> is larger than tier-low; recommend elevating to a PLANNING.md milestone step (or splitting). Exiting without commit."`, exit cleanly, and leave the item un-closed in BACKLOG.md.

**One step per invocation.** Even for a milestone target, this skill implements exactly the _next_ unblocked step and exits. "A milestone advances up to the cap of 3" is the **orchestrator's** behavior (it re-dispatches the same sticky target for the next tick per `AUTONOMOUS_DEV.md §5`), not this skill's. The skill never loops within a milestone.

**Sticky target — never re-derive.** The target comes only from the dispatch inputs. Do not pick different work "because something looks more urgent." The hub owns _which_ target; this skill owns _how_ to implement the next step of it.

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

**Case B — Acceptance criteria ambiguous or unworkable as written.** Do NOT guess. Emit the **`ambiguity`** packet per `PACKETS.md`, then exit (no commit).

**Case C — Step is marked `*(human)*` (detected in Step 1).** The step requires Seth's manual action; the agent cannot advance. Do NOT implement, do NOT skip to the next step. Emit the **`human-action`** packet per `PACKETS.md`, then exit cleanly (no commit).

Log a final line and exit:

```
implementer: step <id> requires human action per STANDARDS §4.2; emitted strategic queue entry <id>. Exiting.
```

Skip Steps 4–6; still write the run-completed record and send the tick-report per Step 7.

**Case D — Mid-step discovery of unmarked human-only work (per `STANDARDS §4.2` "Mid-step discovery").** Triggers when either (a) Step 1's heuristic scan flagged this step upfront, OR (b) during implementation you realize part of the acceptance criteria matches the §4.2 "When to mark" criteria — the pattern list: interactive OAuth flows (`wrangler login`, `gh auth login`, `gcloud auth login`), local-machine verification ("test on physical device," "open in browser"), third-party dashboard configuration ("create account at," "enable in Settings →"), payments, identity verification, App Store / external review waits, physical actions. Critical rule: **do NOT mark the parent step `[x]` based on partial work** — PLANNING.md is the only durable surface for "what's next," and misrepresenting completion there causes downstream runs to fail far from the cause.

Two sub-cases:

**Case D1 — Clean split (default).** Use this when you can cleanly identify which acceptance criteria bullets are agent-doable vs. human-only.

1. Do the agent-doable work normally (per Case A's engineering judgment).
2. Edit `PLANNING.md` inline in this same PR (per the inline-doc-update convention resolved 2026-05-20):
   - Append a parenthetical to the parent step's text: `(split mid-implementation per STANDARDS §4.2; criteria <list> moved to M<n>.<x>a/b/...)`.
   - Insert new `*(human)*` sub-steps immediately after the parent step, named `M<n>.<x>a`, `M<n>.<x>b`, etc. Each sub-step's text restates the human-only criterion as a discrete action.
   - Mark the parent step `[x]` — its remaining (agent-doable) scope is complete.
3. Continue to Step 4 (tests) and Step 5 (adversary loop) normally.
4. After invoking `commit` (Step 6), emit the **`mid-step-split`** packet per `PACKETS.md`, pointing at the new sub-steps. This is in addition to whatever queue/auto-merge action `commit` takes for the PR itself — different ask, different recipient mental model.

**Case D2 — Ambiguous split (fallback).** Use this when you cannot cleanly partition the criteria — they're entangled, or you can't tell what's agent-doable without guessing. Do NOT commit anything. Emit the **`mid-step-ambiguous-split`** packet per `PACKETS.md`.

Log a final line and exit:

```
implementer: step <parent-id> needs split per STANDARDS §4.2; emitted strategic queue entry <id>. Exiting without commit.
```

Skip Steps 4–6; still write the run-completed record and send the tick-report per Step 7.

**Choosing between D1 and D2.** Default to D1. Fall back to D2 only when the partition is genuinely unclear — entangled phrasing, criteria that depend on each other in ways that can't be cleanly separated, or doubt about whether a criterion is human-only. When in doubt, prefer D2; it's better to stop and ask than to restructure PLANNING.md the wrong way and force Seth to unwind it.

### Step 4 — Run tests

If the repo has a test runner (`package.json` `scripts.test`, `pytest.ini`, etc.), run it. If tests fail and the failure is in code this step touched, fix it before continuing. If tests fail in unrelated code, note the failure in the PR body's `## Notes` section and continue — the commit skill's CI-red handling will queue it appropriately.

If no test infrastructure exists, skip this step. Don't create test files speculatively — that's the `test-writer` skill's job, invoked separately.

### Step 5 — Run the adversary review loop

Orchestrate the `pre-commit-reviewer` skill in an iterative loop. The reviewer is **stateless per invocation**; you (the implementer) own the loop state and the convergence decision.

Read `.fleet-ci/.github/scripts/pre-commit-reviewer/SKILL.md` once at the start to understand its contract. Each iteration is one fresh review pass — feed it the current diff plus, on iterations ≥ 2, any pushback rationales from the prior iteration. Use the `run_id` generated at Step 1.

**Loop, max 3 iterations:**

1. Invoke the reviewer per its SKILL.md — pass `run_id`, `iteration_number`, `step_id`, and (if iteration ≥ 2) `prior_findings` + `implementer_pushbacks`.
2. Parse the reviewer's JSON output (`schema_version: 1`).
3. Branch on `outcome`:
   - **`clean`** → exit loop. Continue to Step 6 (commit).
   - **`signoff-with-caveats`** → exit loop. Carry the caveats forward to Step 6; they land in the PR body's `## Adversary review` section and on the activity record. Continue to commit.
   - **`needs-fixes`** → for each blocking finding, decide:
     - **Fix in place** — make the code change the finding calls for. Default action.
     - **Push back** — record a `{ finding_id, rationale }` entry. Push back only when you have a concrete reason (e.g., "this is intentionally out of scope per PRD §3.2," not "this seems fine"). Pushbacks without concrete rationale are wastes of the iteration budget.
   - If you fixed at least one thing, increment `iteration_number` and re-invoke (loop iterates).
   - If you pushed back on everything without fixing, increment and re-invoke with the pushbacks — the reviewer adjudicates.
4. **Cap-hit:** if `iteration_number` reaches 3 and `outcome` is still `needs-fixes`, exit the loop and emit the **`adversary-cap-hit`** packet per `PACKETS.md`. Then exit (no commit). Do NOT push code to a PR when the adversary loop cap-hits — the unresolved blocking findings are the point of the gate.

**Activity-trail writes by this step:** the reviewer writes one record per iteration on its own (per `pre-commit-reviewer/SKILL.md`). The implementer doesn't double-write those; its own bracket (`run-started` / `run-completed`, with `iterations_used` on the latter) is the only loop-level record.

### Step 6 — Invoke the `commit` skill

Read `.fleet-ci/.github/scripts/commit/SKILL.md` and follow its flow. The commit skill handles:

- Tier classification (must-escalate parse + matrix + elevations)
- Inline canonical-doc updates at Step 4b (`PLANNING.md`, `LIFECYCLE.md`, `CHANGELOG.md`, `PRODUCT.md`) — folded into the same commit as the code change. No separate post-merge cleanup phase.
- Branch / commit / push / open PR with the right body composition + labels
- Auto-merge (pre-prod) or queue for approval (post-prod) per the (tier × stage) gating table

The implementer does NOT pre-update those docs itself — the commit skill owns Step 4b. Pass the step ID through so commit can flip the right PLANNING checkbox.

**Pass the work item ID to the commit skill** so the PR body's closing section is populated correctly:

- PLANNING.md step: include "Closes step M<n>.<x>" in the PR body's Summary; commit's Step 4b flips the checkbox in PLANNING.md.
- BACKLOG.md item: include "Closes backlog item BL-<n>" in the PR body's Summary; commit's Step 4b moves the item from BACKLOG.md `## Open` to `## Closed` with date + PR number (per `STANDARDS §4.3` item shape).

**Pass the implementer's `run_id` to the commit skill** (export as `IMPLEMENTER_RUN_ID` env var before invoking) so commit's four mid-flight activity records stitch into the same logical run as the implementer's `run-started` / `run-completed` bracket. Commit's records are required mid-flight observability per `STANDARDS §9` "Commit-skill exit contract" — when the implementer dies inside Step 6 (max-turns, crash), commit's `commit-started` / `tier-classified` / `pr-opened` / `commit-exited` records localize the failure to a phase boundary.

### Step 7 — Exit

When the commit skill returns its terminal `exit_reason` (`auto_merge_initiated`, `queued`, or `exception_emitted`), this skill is done. **Do NOT loop into the next step. Do NOT wait for the PR to actually merge, for CI to go green, or for staging deploy to succeed.** Those are all out-of-process per `STANDARDS §9` "Commit-skill exit contract" — GitHub's native auto-merge, branch protection, CD, and the hub's event-driven merge path own them. The verify gate is the loop boundary.

Forbidden in this step (and anywhere in the implementer):

- `gh pr view --json state,mergedAt` polling loops.
- `gh pr checks <PR>` re-reads after the commit skill's Step 4e captured `<CI_STATUS>`.
- `sleep` + retry on any merge-or-deploy signal.

**Tick-report POST — when `$IMPLEMENTER_SESSION_ID` is non-empty:**

Before logging the final line, POST to the hub's orchestrator endpoint. This is the primary path; the defensive cron sweep handles dropped reports as a fallback.

```bash
if [ -n "$IMPLEMENTER_SESSION_ID" ] && [ -n "$AUTONOMOUS_TICK_TOKEN" ]; then
  curl -sf -X POST \
    -H "Authorization: Bearer $AUTONOMOUS_TICK_TOKEN" \
    -H "Content-Type: application/json" \
    "${IMPLEMENTER_HUB_URL:-https://sethgibson.com}/api/autonomous/tick-report" \
    -d "{
      \"session_id\": \"$IMPLEMENTER_SESSION_ID\",
      \"run_id\": \"$GITHUB_RUN_ID\",
      \"outcome\": \"$TICK_OUTCOME\",
      \"run_url\": \"${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}\",
      \"pr_url\": \"$TICK_PR_URL\",
      \"work_item_id\": \"$TICK_WORK_ITEM_ID\",
      \"queue_entry_id\": \"$TICK_QUEUE_ENTRY_ID\"
    }" \
  || echo "::warning::implementer: tick-report POST failed; defensive cron sweep will reconcile"
fi
```

Set `TICK_OUTCOME` — same value as run-completed's `outcome` when a run-completed record was written; `guard-tripped` for guard trips and Step 1 target-not-found exits. (Hub enum: the six run-completed outcomes plus `guard-tripped`; `workflow-failed` is reserved for the workflow's own failure handler, not this skill.) Set `TICK_WORK_ITEM_ID` (the step/BL-ID picked in Step 1), `TICK_PR_URL` (the PR URL from the commit skill — same URL logged in the run-completed record; empty string if no PR was opened), and `TICK_QUEUE_ENTRY_ID` (the queue entry ID emitted by the commit skill, if any — commit skill logs this as `queue_entry_id: <id>`). Omit `pr_url`, `queue_entry_id`, and `work_item_id` from the JSON if empty.

Log a final line:

```
implementer: step M<n>.<x> complete; PR <url>; gate=<auto-merge|queue|exception>. Exiting.
```

---

## Activity trail writes

The implementer brackets its own run with two activity records per `STANDARDS.md` Agent Activity Trail section — **`run-started`** (immediately after Step 1 resolves the work item) and **`run-completed`** (at exit on EVERY path after run-started was written: Cases B/C exits, cap-hit exits, successful commits). Templates and graceful-failure handling are in `PACKETS.md` § Activity records.

Guard trips and Step 1 target-not-found exits happen before the run-started record, so they write no activity records (tick-report only). The reviewer (Step 5) writes its own per-iteration records; this skill does NOT double-write those. The Agent Activity Trail UI relies on a paired start/complete to render run duration correctly.

---

## Failure modes

| Failure                                                         | Behavior                                                                                                                                                                                                                                             |
| --------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Guard 1–2 trip, or Step 1 target not found / no qualifying step | Exit cleanly with `::warning::` / `::notice::` log line. No queue entry, no activity record (run hadn't started). Tick-report outcome: `guard-tripped`.                                                                                              |
| Next step is marked `*(human)*` (`STANDARDS §4.2`)              | Emit `strategic` queue entry per Step 3 Case C; write `run-completed` record with `outcome: "blocked-human"`; exit cleanly. No commit, no attempt to skip ahead.                                                                                     |
| Step implementation fails (cannot determine what to do)         | Emit `strategic` queue entry per Step 3 Case B; write `run-completed` with `outcome: "blocked-strategic"`; exit.                                                                                                                                     |
| Tests fail in code this step touched and agent can't fix        | Continue to commit; note in PR body `## Notes`. The commit skill's CI-red handling flips would-be-auto-merge to MEDIUM queue. `run-completed` outcome is `"queued-for-approval"` or `"committed"` depending on commit gate.                          |
| Tests fail in unrelated code                                    | Note in `## Notes`; continue. Same CI-red flow if CI catches it.                                                                                                                                                                                     |
| Adversary loop returns `clean` or `signoff-with-caveats`        | Continue to Step 6 (commit).                                                                                                                                                                                                                         |
| Adversary loop cap-hits (`needs-fixes` on iteration 3)          | Emit `exception` queue entry per Step 5; write `run-completed` with `outcome: "blocked-adversary-cap-hit"`; exit. No commit.                                                                                                                         |
| Reviewer SKILL.md missing from `.fleet-ci/`                     | Log `::warning::implementer: pre-commit-reviewer SKILL.md missing; skipping adversary loop` and proceed to Step 6. This is a CI configuration failure; don't gate work on it. Operator notices via the missing review records in the activity trail. |
| Commit skill exits cleanly (auto-merged or queued)              | Implementer exits success; `run-completed` outcome is `"committed"` or `"queued-for-approval"`.                                                                                                                                                      |
| Commit skill emits exception entry                              | Implementer exits success — the exception entry is the right surface for the failure, not a duplicate workflow failure. `run-completed` outcome is `"queued-for-approval"`.                                                                          |
| `claude-code-action` itself errors                              | The workflow's error handling fires; if a queue entry for that case is wanted, the central workflow's `if: failure()` path emits one (like security-review's pattern). `run-completed` may not write — workflow-level failure, not skill-level.      |
| `QUEUE_SERVICE_ROLE_KEY` missing                                | Commit skill's graceful fallback fires; reviewer and implementer activity writes both skip with `::warning::`. The skill itself doesn't fail; the operator notices via missing queue entry and missing activity records.                             |

---

## Concurrency

The agent does NOT enforce concurrency. The central workflow does, via `concurrency: { group: implementer-${{ github.repository }}, cancel-in-progress: false }`. This serializes implementer runs per-product — a queued second invocation waits for the first to finish rather than racing.

---

## What this skill does NOT do

- **Multiple work items per run.** One PLANNING.md step OR one BACKLOG.md item per invocation; exits after commit. The verify gate is the loop boundary. Never mixes the two surfaces in a single run — the hub picker owns prioritization.
- **Skip past `*(human)*`-marked steps.** Those are hard blockers per `STANDARDS §4.2`. Emit `strategic` queue entry (Step 3 Case C) and exit; do not advance to the next unchecked step. If Seth wants out-of-order execution, he reorders PLANNING.
- **Decide tech-stack or major architecture.** Those are HIGH tier per `§9` elevation rules and always queue. If a step's acceptance criteria require such a decision, emit a `strategic` queue entry and exit (per Step 3 Case B ambiguity flow).
- **Modify PLANNING.md itself beyond what's needed for the completed step.** The commit skill's Step 4b owns inline doc updates (flipping the completed checkbox, refreshing LIFECYCLE/CHANGELOG/PRODUCT). Restructuring or re-prioritizing PLANNING is Seth's lane.
- **Make creative or strategic calls.** Per `STANDARDS.md §10` — those queue as `strategic` entries. The agent prepares; Seth decides.
- **Wait for queue entry resolution.** The skill exits after commit; the hub orchestrator resumes the loop.
- **Self-debug claude-code-action infrastructure issues.** Token expiry, quota exhaustion, etc. surface as workflow failures; the operator (or a future ops agent) handles them.
