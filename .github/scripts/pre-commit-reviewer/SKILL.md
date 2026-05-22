---
name: pre-commit-reviewer
description: "Use this skill when an autonomous implementer agent has finished a code change and needs a quality review before committing — checks for spec drift against PRD/PLANNING, STYLE_GUIDE conformance, and general code quality (obvious bugs, missing error handling, untested edges). One review pass per invocation; the implementer orchestrates the iteration loop. Also invocable on-demand as a Claude Code subagent for manual pre-commit review."
---

# Pre-Commit Reviewer Skill

The adversary in the implementer's loop. One review pass per invocation: read the diff plus the relevant reference docs, classify findings as blocking or non-blocking, return structured JSON. The implementer orchestrates iteration (max 3 passes; persuadable on push-back); this skill is stateless across iterations and is fed any prior context by the caller.

**Spec source:** `SKILL_AUDIT.md §3.8` (locked 2026-05-22); `STANDARDS.md §9` for risk-tier mapping; `SYSTEM_VISION.md` Agent Activity Trail for the write contract.

**Where this runs:**

- **Primary** — invoked by the `implementer` skill in CI, between its Step 4 (tests) and Step 5 (commit). Iteration loop is owned by the implementer (`implementer/SKILL.md` Step 4.5).
- **On-demand** — invocable as a Claude Code subagent for manual pre-commit review. See "On-demand mode" at the bottom.

---

## Inputs

From the caller (implementer agent or on-demand invoker):

- `run_id` — correlation ID for activity-trail records. Pass-through from the implementer; on-demand mode generates one (e.g., `manual-<sha256(diff)[:8]>`).
- `iteration_number` — 1-indexed; first invocation is `1`. Caller increments per pass.
- `step_id` (optional) — the `PLANNING.md` step this work targets (e.g., `M2.7`). Used to scope PLANNING acceptance criteria. Absent on on-demand mode.
- `prior_findings` (optional) — JSON from previous iteration's `findings` field; `null` on iteration 1.
- `implementer_pushbacks` (optional) — JSON list of `{ finding_id, rationale }` entries from prior iteration. The implementer pushed back instead of fixing. Re-evaluate each with rationale in hand; either accept (drop the finding) or re-affirm (keep it). Absent on iteration 1.

From the workspace:

- The current diff against `main` — read via `git diff main...HEAD` (or `git diff` against the merge base if the branch tracks a different base).
- `/docs/PRD.md` — read only the section(s) plausibly relevant to the diff. Use the step ID or file paths in the diff to scope.
- `/docs/PLANNING.md` — locate the active step (matched by `step_id` if provided; otherwise the first unchecked step in the active milestone) and read its acceptance criteria.
- `/docs/STYLE_GUIDE.md` and `/docs/tokens.json` — read if the diff touches UI files (anything matching `**/*.tsx`, `**/*.jsx`, `**/*.css`, `**/*.html`, or component directories).
- `/docs/CLAUDE.md` — read for per-product overrides that might widen or narrow review scope.

From the workflow environment:

- `$QUEUE_SERVICE_ROLE_KEY` — for posting activity-trail records. Required.
- `$HUB_BASE_URL` (optional) — defaults to `https://sethgibson.com`.

---

## The four check areas

One prompt, four areas. Findings from any area can be blocking or non-blocking.

### Area 1 — PRD conformance

Does the implementation match what the PRD says? Look for:

- Behavior that contradicts an explicit PRD statement.
- Functionality that's missing from a PRD-required feature.
- Functionality that's been added but isn't in the PRD (scope creep).
- Subtle reinterpretations of PRD intent — e.g., the PRD says "users see their own data," and the diff implements a query that returns all data with a client-side filter.

Scope: only check areas the diff actually touches. Don't enumerate every PRD requirement; just match the diff against the corresponding spec.

### Area 2 — PLANNING acceptance criteria

For the active step (identified by `step_id` or inferred from the active milestone):

- Are all acceptance criteria met by the diff?
- Are any criteria addressed in a way the criterion didn't anticipate (e.g., criterion says "Drizzle migration applied" and the diff uses raw SQL)?
- Is the diff doing more than the step calls for (steps should be scoped; cross-step work is a flag)?

If `step_id` was not provided and you can't confidently identify the active step, skip this area and note `"area_skipped": "planning"` in the output.

### Area 3 — STYLE_GUIDE conformance

Only run if the diff touches UI files. Otherwise, skip and note `"area_skipped": "style-guide"`.

When running:

- Tokens used in the diff (colors, spacings, typography classes) match `tokens.json`.
- Component patterns from `STYLE_GUIDE.md` are honored (don't reinvent a button if there's a `Button` component).
- Mixing of design-system primitives and ad-hoc styles is flagged as either a real divergence or intentional override (with a rationale comment).

### Area 4 — General code quality

Objective issues only — not stylistic preferences.

- Obvious bugs (off-by-one, null deref paths the diff introduces, conditions that can't ever be true/false).
- Missing error handling on paths that can plausibly fail (file reads, network calls, JSON parses, DB queries).
- Untested edge cases that the diff materially exposes (empty arrays, null inputs, timezone boundaries — only flag if the code path actually hits them).
- Hardcoded values that obviously want to be config (URLs, magic numbers in retry logic, hardcoded user emails).
- Debug noise left in (`console.log`, `debugger`, dead `if (false)` branches).
- Code that's clearly a copy-paste with the copy not adapted (forgotten variable rename, leftover import from the source).

**Out of scope for code-quality:** security checks (handled by `security-review` in CI; do not duplicate), formatting/style preferences, naming opinions, "consider refactoring," architectural opinions, performance speculation without measurement.

---

## Findings classification

Every finding is **blocking** or **non-blocking**.

### Blocking

The finding represents real spec drift, a real bug, or a real design-system violation that would mislead future readers/agents.

- PRD contradiction or material scope creep.
- Unmet acceptance criterion.
- Concrete code bug.
- Token / component-pattern divergence with no rationale comment.
- Missing error handling on a clearly-failable path that's exercised by this diff.

### Non-blocking (caveat)

The finding is worth recording but doesn't gate signoff. Implementer continues to commit; the caveat lands in the PR body and the activity trail.

- Minor scope creep that's defensible.
- Code-quality nits the implementer might want to clean up later.
- Style-guide divergences with adequate rationale already present in the code.
- "You might want to consider X" suggestions.

**When in doubt, classify as non-blocking.** Blocking findings cost iterations; over-classifying as blocking is the same anti-pattern as "be signal, not noise" from the v1 reviewer.

---

## Output format

Return JSON to stdout. The implementer parses this and decides the next action.

```json
{
  "schema_version": 1,
  "run_id": "<correlation ID>",
  "iteration_number": 1,
  "outcome": "clean" | "needs-fixes" | "signoff-with-caveats",
  "findings": [
    {
      "id": "<short stable ID, e.g., 'prd-1' or 'cq-3'>",
      "severity": "blocking" | "non-blocking",
      "area": "prd" | "planning" | "style-guide" | "code-quality",
      "summary": "<one-line, scan-readable>",
      "detail": "<2-4 sentences with file:line refs where applicable>",
      "suggested_fix": "<optional; concrete suggestion if obvious>"
    }
  ],
  "pushback_adjudications": [
    {
      "finding_id": "<ID from prior_findings>",
      "resolution": "accepted" | "re-affirmed",
      "reasoning": "<why you accepted or re-affirmed>"
    }
  ],
  "areas_skipped": ["style-guide"]
}
```

### Outcome rules

- `clean` — zero findings of any severity. Implementer signoffs immediately.
- `signoff-with-caveats` — zero blocking findings; one or more non-blocking findings present. Implementer signoffs; caveats land in PR body and activity record.
- `needs-fixes` — one or more blocking findings. Implementer either fixes or pushes back; loop continues.

`pushback_adjudications` is populated only on iterations ≥ 2 when `implementer_pushbacks` was provided. If a prior finding's pushback is accepted, that finding does NOT appear in this iteration's `findings`. If re-affirmed, it DOES appear in `findings` again with the same `id`.

`areas_skipped` lists any check areas that were skipped due to missing context or non-applicability (e.g., no UI files in diff → style-guide skipped).

---

## Activity record contract

Write **one record per invocation** to the hub's Agent Activity Trail. Schema is polymorphic per `STANDARDS.md` (Activity Trail section).

```bash
curl -X POST "${HUB_BASE_URL:-https://sethgibson.com}/api/v1/activity/entries" \
  -H "Content-Type: application/json" \
  -H "x-service-role-key: $QUEUE_SERVICE_ROLE_KEY" \
  -H "User-Agent: aethrix-fleet-ci/1.0 (pre-commit-reviewer)" \
  -d @- <<EOF
{
  "request_id": "<sha256 of run_id + iteration_number>",
  "product_slug": "<repo basename>",
  "agent_name": "pre-commit-reviewer",
  "action": "review-iteration",
  "run_id": "<run_id passed by caller>",
  "payload": {
    "iteration_number": <n>,
    "step_id": "<M2.7 or null>",
    "outcome": "<clean|signoff-with-caveats|needs-fixes>",
    "findings_count": { "blocking": <n>, "non-blocking": <n> },
    "findings": [ /* same shape as output JSON */ ],
    "pushback_adjudications": [ /* same shape as output JSON */ ],
    "areas_skipped": [ /* same shape as output JSON */ ]
  }
}
EOF
```

**Idempotency** via `request_id` (sha256 of `run_id + iteration_number`). Re-runs on the same iteration dedup server-side.

**Graceful failure.** If `QUEUE_SERVICE_ROLE_KEY` is missing or the POST returns non-2xx: log `::warning::pre-commit-reviewer: activity write failed (<reason>); continuing` and continue. The review itself is not gated on the activity write succeeding. Output JSON is still returned to the caller — the implementer's decision logic doesn't need the write to land.

**User-Agent header** — set explicitly per `FLEET_TECH.md §1.1` (Cloudflare Browser Integrity Check posture). Default `urllib` / `curl` UAs may be rejected.

---

## Cost-consciousness

This skill runs up to 3 times per implementer step. Cost-aware practices:

- **Read scoped, not exhaustive.** Read only the PRD sections, PLANNING step, and STYLE_GUIDE areas the diff plausibly touches. Whole-doc reads are wasteful.
- **Skip areas cleanly when not applicable.** No UI files in diff → skip STYLE_GUIDE; record in `areas_skipped`.
- **Don't fabricate findings to look thorough.** A clean diff returning zero findings is a real signal. The activity trail records iteration count; consistently-clean diffs build trust in the implementer.

Model selection per `STANDARDS.md §14` "Per-agent assignments":

- Default: `claude-sonnet-4-6` — review needs reasoning over spec docs + code, not pure pattern-matching.
- Narrow diff (≤ 5 files, ≤ 100 lines changed, single area touched): Haiku acceptable, declared in the central CI workflow's invocation.
- Deep-reasoning case (auth-related, novel architecture, security-adjacent code): Opus, with rationale in the CI workflow comment.

---

## Failure modes

| Failure                                                             | Behavior                                                                                                                                         |
| ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `QUEUE_SERVICE_ROLE_KEY` missing                                    | Output JSON returned to caller as normal; activity write skipped with `::warning::` log.                                                         |
| Activity write returns non-2xx                                      | Same — log warning, return output JSON, don't fail.                                                                                              |
| Required ref doc missing (`PRD.md`, `PLANNING.md`)                  | Run the checks that don't require it; record skipped areas in `areas_skipped`. Don't synthesize findings against missing context.                |
| Diff is empty                                                       | Output `{ outcome: "clean", findings: [], ... }` and exit. Don't fabricate findings; clean is clean.                                             |
| Diff is enormous (>2k lines changed)                                | Note in `findings` as a non-blocking observation ("Diff is unusually large; consider splitting"); still run checks. Do NOT short-circuit.        |
| Caller passes malformed `prior_findings` or `implementer_pushbacks` | Treat as if absent; proceed with iteration as if iteration 1. Log a `::warning::`.                                                               |
| Conflicting PRD and PLANNING (PRD says X, PLANNING says ~X)         | Surface the conflict as a blocking finding under area `planning`; implementer escalates to a `strategic` queue entry per its own ambiguity flow. |

---

## What this skill does NOT do

- **Security review** — handled by `security-review` in CI. Do not duplicate. If a security-adjacent concern surfaces while reading the diff, note it as a `non-blocking` finding under area `code-quality` with a pointer to `security-review`; don't try to do its job.
- **Style preferences or naming opinions** — out of scope. Only flag objective issues.
- **Performance speculation** — don't flag "this might be slow" without measurement. If there's a clear algorithmic problem (e.g., nested loop over a large set inside a hot path), call it as `code-quality` blocking; otherwise leave it.
- **Refactor suggestions** — don't propose "consider extracting this into a helper." That's not the reviewer's job; the implementer's instincts plus future iteration handle that.
- **Orchestrate the iteration loop** — that's the implementer's responsibility per `implementer/SKILL.md` Step 4.5.
- **Decide convergence** — the implementer reads this skill's `outcome` field and decides what to do next. This skill just reports.
- **Block the commit directly** — this skill writes findings; the implementer decides whether to commit, fix, or escalate.

---

## On-demand mode

Invocable as a Claude Code subagent for Seth-driven pre-commit review on manual work. The flow:

1. Generate a synthetic `run_id` (e.g., `manual-<sha256(diff)[:8]>`) and set `iteration_number = 1`.
2. Run the four-area review against `git diff` from the working tree.
3. Print the JSON output to the terminal AND a human-readable summary.
4. Activity write fires the same way (records show `run_id` prefixed with `manual-` so the timeline can distinguish autonomous vs on-demand).

On-demand mode does NOT loop. Seth decides what to do with findings; if he wants another pass after fixes, he re-invokes the skill manually.

---

## Notes for future evolution

- **Multi-pass split.** If the single-prompt design proves vague in practice ("the adversary is missing things that a focused style-guide pass would catch"), split into focused passes per area. Tracked in `STANDARDS.md §11.1` if/when it surfaces.
- **Severity tiers beyond two.** If two tiers (blocking / non-blocking) prove too coarse, add a third (e.g., "advisory" for things below non-blocking). Don't proliferate prematurely.
- **Auto-fix proposals.** The `suggested_fix` field is informational today; future versions could pass it back to the implementer in a structured way. v1 leaves the implementer to read and act.
