---
name: commit
description: "Use this skill whenever the user wants to save their work to version control — even if they phrase it casually. Triggers on: \"commit\", \"commit my changes\", \"ship this\", \"push this up\", \"create a PR\", \"open a pull request\", \"let's save this\", \"wrap this up\", or anything suggesting they want their current changes in git. Reads `LIFECYCLE.md` stage + classifies the diff's risk tier to decide whether to auto-merge, queue for approval at the hub, or block. Always use this skill for any git commit or PR action, even if the request seems simple."
---

# Commit Skill

The gate between work and version history. Runs autonomously: the implementation agent invokes this when a `PLANNING.md` step is complete and tests pass. Also runs on manual trigger from Seth ("commit", "ship it", etc.).

Wraps a feature in a PR, classifies the diff's risk, and routes to one of three outcomes:

- **Auto-merge** on green CI (pre-prod stages, or low-tier in beta, or low-tier in production)
- **Queue for approval** at the hub (medium/high-tier post-prod, or must-escalate match)
- **Block** with an exception queue entry (CI red, adversary loop didn't converge, etc.)

The skill never asks Seth a real-time question. All interaction is via the hub's decision queue per `STANDARDS.md §7`.

References: `STANDARDS.md §7` (queue spec), `§8` (escalation packet), `§9` (risk matrix + gating table + must-escalate), `§17` (fleet CI centralization), `COMMIT_REDESIGN.md` (full design spec).

---

## Prerequisites

- `git` and `gh` (GitHub CLI) available and authenticated.
- Repo has `/docs/LIFECYCLE.md` with a `stage:` header per `STANDARDS.md §4`.
- For queue emission: `QUEUE_SERVICE_ROLE_KEY` env var must be set to the hub's service-role secret. The hub URL defaults to `https://sethgibson.com` — override with `HUB_BASE_URL` if needed.
- If `QUEUE_SERVICE_ROLE_KEY` is missing AND the diff routes to "queue for approval", fall through to the legacy "wait for confirmation in chat" behavior and emit a warning that the queue write was skipped.

---

## Steps

### Step 1: Inspect

```bash
git status
git diff --stat
git diff main...HEAD 2>/dev/null || git diff HEAD
```

Read the diff carefully — you'll use it for the branch name, commit message, tier classification, and PR body.

**If the working tree looks off,** stop and tell Seth:
- No changes staged or unstaged → nothing to commit.
- Untracked files that look unintentional → list them; ask before including.

Also read up front (cached for later steps):

```bash
head -10 docs/LIFECYCLE.md 2>/dev/null || head -10 LIFECYCLE.md
```

Extract `stage:` and `monetized:` from the frontmatter header. Missing or unparseable → default to `stage: production`, `monetized: true` (fail up). Note this in the PR body as "LIFECYCLE.md repair needed."

### Step 2: Adversary loop

Invoke the `pre-commit-reviewer` skill (or `@quality-gate` subagent if available) to review the diff:

- All checks pass → continue automatically.
- Findings returned → record them for the PR body's `## Adversary review` section (Step 4). For autonomous runs, iterate with the implementer agent until findings are addressed; if convergence isn't reached within a reasonable cap, emit an exception queue entry per Step 5 Route C and stop. The formal convergence rule is open gap G14 in `STANDARDS.md §11.1` — apply judgment until the spec lands.

> **Note:** `security-review` runs in CI (per `STANDARDS.md §17` — central workflow at `Aethrix-Labs/.github`). Don't invoke it here. Its findings appear in the PR body via the security-review composer; this skill consumes them at Step 4.

### Step 3: Classify risk tier

Run in order. First match short-circuits:

**3a. Must-escalate check.** If `/docs/CLAUDE.md` has a `## Must Escalate` section, parse the bullets and test each pattern against the diff. Patterns (per `STANDARDS.md §9` and `COMMIT_REDESIGN.md §5`):

- `path:<glob>` — file path matches glob (e.g. `path:src/auth/**`)
- `filename:<glob>` — basename matches glob in any directory (e.g. `filename:*.env*`)
- `content:contains <string>` — added or modified diff lines contain the literal string (e.g. `content:contains STRIPE_SECRET_KEY`)

Any match → tier = HIGH, rationale = `"must-escalate match: <pattern>"`. Stop.

**3b. Matrix lookup.** Walk the `STANDARDS.md §9` matrix and find the row(s) the diff touches. Multiple rows → highest tier wins. Output: a candidate tier + the row name(s) matched.

**3c. Elevation rules** (from `STANDARDS.md §9`, apply in order):

- Touches auth, session, payments, PII, or financial data → tier = HIGH.
- Monetized product (`monetized: true`) + user-facing change → bump +1 tier.
- Irreversible without real effort (data migrations, anything published or emailed) → at least MEDIUM.
- Cross-product / fleet-wide effect → +1 tier minimum.
- When uncertain → bump up one tier (never guess down).

**3d. Tech-stack or major architecture proposals** → always HIGH (per `STANDARDS.md §9` elevation rule). This skill only sees this case if someone bypassed `tech-stack-advisor`.

Output: a single `tier ∈ {low, medium, high}` plus a one-line rationale string. Both flow into the PR body and the queue packet.

### Step 4: Branch, update canonical docs inline, commit, push, open PR

> **Step 4 must complete before Step 5 starts.** Step 5 reads Step 4's output bundle (`<TIER>`, `<RATIONALE>`, `<STAGE>`, `<PR_NUMBER>`, `<PR_URL>`, `<CI_STATUS>`, `<MUST_ESCALATE_HIT>`, `<ADVERSARY_CONVERGED>`). Do not skip ahead — Step 5's first action is a guard that fails loudly if these are missing.

**4a. Branch.**

```bash
git checkout -b <branch-name>
```

**Branch name:** kebab-case, short and descriptive. Examples: `add-google-sso`, `fix-auth-redirect`, `dashboard-layout`.

**If already on a feature branch** (not `main`): confirm with Seth before creating another branch on top.

**4b. Update the canonical docs inline as part of this same commit.** Doc updates that describe what just shipped MUST land in the same PR as the code change — not as a separate post-merge cleanup PR. Atomicity (the code and the docs claiming it shipped revert together), review surface (Seth sees the agent's claims about what changed alongside the diff), and PLANNING-authority-at-merge-time all favor inline. This is what the agent does in practice; the pattern is documented here so the next product onboarding doesn't relearn it.

The four docs to consider, in order:

- **`PLANNING.md`** — flip the completed step's checkbox to `[x]`. If multiple sub-bullets were the step's acceptance criteria, flip those too. Be conservative — only check off what was actually fully completed by this PR. Partial → leave unchecked and add `*(partial — X done)*` in the step body.
- **`LIFECYCLE.md`** — bump `last_updated:` to today's date. Update `next_milestone:` to whatever's next in `PLANNING.md`. If the next step is marked `*(human)*` per `STANDARDS §4.2`, propagate the `(human)` annotation into `next_milestone:` so the hub dashboard surfaces the manual-action state at a glance. Body sections only if state materially shifted.
- **`CHANGELOG.md`** — prepend a new entry with today's date and a short title. Include both a `**User-facing:**` section (what end-users notice — omit if pure infra) and a `**Developer:**` section (what changed in the repo, in terms a maintainer cares about). Match the existing file's date and heading conventions if one exists.
- **`PRODUCT.md`** — only update if this PR changed user-facing product knowledge (in-app FAQ, help docs, agent-readable feature descriptions). Per `SKILL_AUDIT §3.9` this is `PRODUCT.md`'s scope. Pure scaffolding / infra / refactors don't touch it. `ARCHITECTURE.md` updates remain out-of-scope (gap G13 — flag material architecture changes in `## Notes` of the PR body instead).
- **`BACKLOG.md`** — if the PR body's `## Summary` contains a line matching `Closes backlog item BL-<n>`, move that item from `## Open` to `## Closed` and append `*(closed: <today's date>, PR #<n>)*` to its trailing metadata. This detection runs **independently** of the PLANNING.md checkbox-flip — a single PR can close both a backlog item and a PLANNING.md step; handle both detections. If the `## Closed` section is absent, create it at the end of the file. Do not touch BACKLOG.md if no `Closes backlog item` line is present.

**4c. Commit and push.**

```bash
git add -A
git commit -m "<type>: <short imperative summary>

<One or two sentences explaining what and why, if non-obvious.>"
git push -u origin <branch-name>
```

**Conventional commit style:** check `git log --oneline -5`. If the repo uses `feat:`/`fix:`/`chore:`/etc., follow that. Otherwise plain imperative summary.

**4d. Open the PR with the three always-on labels.**

Apply `tier:<TIER>`, `stage:<STAGE>`, and `commit-pending-merge` at open time, unconditionally. These three are audit/visibility labels, not routing artifacts — they apply on every PR this skill opens regardless of the eventual route. The fourth label (`auto-merge`) is applied later in Step 5 only if the decision tree lands on Route A.

```bash
gh pr create --base main \
  --title "<COMMIT_TITLE>" \
  --body "<PR_BODY>" \
  --label "tier:<TIER>" \
  --label "stage:<STAGE>" \
  --label "commit-pending-merge"
```

Capture the returned PR URL and number; both feed Step 5.

**4e. Capture CI status.**

```bash
gh pr checks <PR_NUMBER> --json bucket,name,state
```

Reduce to a single value: `failed` if any required check has bucket `fail`; otherwise `pending` if any check is still running; otherwise `pass`. Record as `<CI_STATUS>`.

**4f. Emit the Step 4 output bundle** (Step 5 reads these by name; do not proceed without them):

| Name | Source | Example |
| --- | --- | --- |
| `<TIER>` | Step 3 output | `low` |
| `<RATIONALE>` | Step 3 output | `matrix row: code — non-functional` |
| `<STAGE>` | `LIFECYCLE.md` | `in-development` |
| `<MUST_ESCALATE_HIT>` | Step 3a result | `false` |
| `<ADVERSARY_CONVERGED>` | Step 2 result | `true` |
| `<CI_STATUS>` | Step 4e | `pending` |
| `<PR_NUMBER>` | Step 4d return | `42` |
| `<PR_URL>` | Step 4d return | `https://github.com/.../pull/42` |
| `<PR_TITLE>` | Step 4c commit summary | `feat: add SSO redirect` |
| `<PRODUCT_SLUG>` | repo name or `LIFECYCLE.md` | `puzzle-pop` |
| `<PLANNING_ANCHOR_URL>` | derived from `PLANNING.md` step ref | `https://github.com/.../PLANNING.md#m07` |
| `<ADVERSARY_SUMMARY>` | Step 2 result string | `converged, no findings` |
| `<TESTS_SUMMARY>` | `test-writer` output or `none` | `3 added, 0 untestable` |
| `<RECOMMENDATION>` | composed from Step 2 + 4e | `Approve — all checks green` |

**If `<ADVERSARY_CONVERGED> == false`,** Step 4d/4e/4f beyond this point are skipped (no PR is opened on Route C). In that case the bundle only requires `<TIER>`, `<RATIONALE>`, `<STAGE>`, `<MUST_ESCALATE_HIT>`, `<ADVERSARY_CONVERGED>`, `<PRODUCT_SLUG>`; the PR-related fields are absent by design. Step 5's guard accounts for this.

### PR body composition (per `COMMIT_REDESIGN.md §8`)

Fixed section order, conditional inclusion. Plain Markdown.

```markdown
## Summary
<One paragraph: what this PR does and why.>

## PLANNING.md step
- [x] M<n>.<step> — <step text>
<Link to the PLANNING.md anchor. Omit section if commit isn't implementation-loop-triggered.>

## Risk tier
**<tier>** — <rationale string from Step 3>

## Adversary review
<pre-commit-reviewer findings summary. Omit if loop didn't run or returned clean.>

## Security review
<!-- security-review:start --><!-- security-review:end -->
<Sentinel for the central security-review workflow's PR body composer per STANDARDS.md §17. The composer fills this in post-CI; leave the empty sentinels here.>

## Tests
<test-writer output summary: tests added, coverage delta, untestable gaps. Omit if test-writer didn't run.>

## Rollback
<Concrete revert instructions. Required for medium/high tier. Omit for low.>

## Notes
<Anything Seth would want to know. Optional.>
```

**Truncation order if the PR body exceeds GitHub's size cap:** `Notes` → `Adversary review` → `Tests`. Never truncate `Summary`, `Risk tier`, `Rollback`.

### Step 5: Gate — auto-merge, queue, or block

This step is **mechanical**. Walk the decision tree top-down to a single terminal route, then run that route's pre-written command block, substituting placeholders only. Do not compose new shell commands. Do not re-interpret the §9 table — the tree below is the §9 table, pre-walked.

**5a. Guard — verify Step 4 outputs exist.**

Before touching the decision tree, confirm the bundle is set. The required set depends on whether the adversary loop converged:

- **Always required** (every run): `<TIER>`, `<RATIONALE>`, `<STAGE>`, `<MUST_ESCALATE_HIT>`, `<ADVERSARY_CONVERGED>`, `<PRODUCT_SLUG>`.
- **Additionally required when `<ADVERSARY_CONVERGED> == true`** (i.e., Step 4d/4e ran and a PR exists): `<CI_STATUS>`, `<PR_NUMBER>`, `<PR_URL>`, `<PR_TITLE>`, `<PLANNING_ANCHOR_URL>`, `<ADVERSARY_SUMMARY>`, `<TESTS_SUMMARY>`, `<RECOMMENDATION>`.

If any required name is missing, **stop immediately** and emit:

> Step 5 guard failed — Step 4 outputs missing: `<list-of-missing-names>`. Re-run Step 4 before proceeding.

Do not pick a route. Do not attempt a merge or queue write. Missing outputs mean Step 4 didn't finish; pressing on produces exactly the brittleness the §11.3 entry resolves.

**5b. Decision tree — terminate on the first matching branch.**

Walk top-down. The first matching condition is the terminal route. Do not "average" branches or look further down the tree.

```
1. Is <ADVERSARY_CONVERGED> == false?
     YES → Route C  (exception: adversary loop did not converge)

2. Is <MUST_ESCALATE_HIT> == true?
     YES → Route B  (queue, tier forced to HIGH per §9)

3. Is <CI_STATUS> == failed?
     YES → Route B  (queue, tier forced to MEDIUM per §9 / §7 override)

4. (CI is pass or pending; no must-escalate; adversary converged.)
   Branch on <STAGE>:

     <STAGE> in {idea, mvp, in-development, graduated, deprecated}
         → Route A  (auto-merge, any tier)

     <STAGE> == beta
         <TIER> in {low, medium} → Route A
         <TIER> == high          → Route B

     <STAGE> == production
         <TIER> == low                  → Route A
         <TIER> in {medium, high}       → Route B

     <STAGE> unrecognized
         → Route B  (fail up per §9 stage-default rule; note in packet)
```

Every leaf is one of {Route A, Route B, Route C}. There are no ambiguous cases.

**5c. Apply the `auto-merge` label iff Route A.**

Mechanical, runs immediately after the route is decided. Skip if the route is B or C.

```bash
gh pr edit <PR_NUMBER> --add-label "auto-merge"
```

**5d. Run the route's pre-written command block.** Substitute placeholders only.

---

**Route A — auto-merge** (pre-written; substitute `<PR_NUMBER>` only):

```bash
gh pr merge <PR_NUMBER> --auto --squash
```

Then poll merge state for up to ~3 minutes (six iterations, ~30s each):

```bash
gh pr view <PR_NUMBER> --json state,mergedAt
```

- `state == MERGED` → proceed to Step 6.
- 3-min timeout → tell Seth CI is slow, present `<PR_URL>`, exit cleanly. The merge fire-back loop will pick the PR up when CI finishes.

---

**Route B — queue for approval** (pre-written; substitute placeholders only):

```bash
curl -X POST "${HUB_BASE_URL:-https://sethgibson.com}/api/v1/queue/entries" \
  -H "Content-Type: application/json" \
  -H "x-service-role-key: ${QUEUE_SERVICE_ROLE_KEY}" \
  -d @- <<'JSON'
{
  "request_id": "<SHA256_OF_PR_URL>",
  "entry_type": "approval",
  "risk_tier": "<TIER_FOR_PACKET>",
  "agent_name": "commit",
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
JSON
```

Placeholder rules for Route B:

- `<TIER_FOR_PACKET>` = `high` if `<MUST_ESCALATE_HIT> == true`; else `medium` if reached via the CI-failed branch; else `<TIER>` unchanged.
- `<SHA256_OF_PR_URL>` = `sha256(<PR_URL>)` — idempotent on re-run.
- `<CI_STATUS_SUMMARY>` = `green` if `<CI_STATUS> == pass`; `pending` if `pending`; `red — <failing-check-name>` if `failed`.
- `<ADVERSARY_SUMMARY>`, `<TESTS_SUMMARY>`, `<RECOMMENDATION>` = short summary strings carried from Steps 2 and 4.
- `<PR_TITLE>`, `<PRODUCT_SLUG>`, `<PR_NUMBER>`, `<PR_URL>`, `<PLANNING_ANCHOR_URL>` = from Step 4 outputs.

**Note on `product_id`:** the API expects a hub-side `products.id` (UUID), not a slug. v0 omits the field and encodes the product slug in `title` (the `[<PRODUCT_SLUG>]` prefix). Slug→ID resolution at the hub is tracked as a `§11.1` follow-up.

**On 2xx:** tell Seth (or the calling agent):

> PR #<PR_NUMBER> opened and queued for approval — `<PR_URL>`. Skill exiting; merge fire-back loop resumes after Seth approves.

**On non-2xx, or `QUEUE_SERVICE_ROLE_KEY` missing:** fall back to legacy in-chat approval; leave the PR open; do not auto-merge:

> PR #<PR_NUMBER> needs your approval (stage: `<STAGE>`, tier: `<TIER>`). **[Review on GitHub](<PR_URL>)**
> Queue write failed (`<reason>`) — let me know when you've merged it.

---

**Route C — block (exception)** (pre-written; substitute placeholders only):

The PR is NOT opened in Route C — the diff stays on the local branch. Step 4d/4e are skipped when Step 2 already failed convergence; emit the exception entry directly.

```bash
curl -X POST "${HUB_BASE_URL:-https://sethgibson.com}/api/v1/queue/entries" \
  -H "Content-Type: application/json" \
  -H "x-service-role-key: ${QUEUE_SERVICE_ROLE_KEY}" \
  -d @- <<'JSON'
{
  "request_id": "<SHA256_OF_BRANCH_PLUS_TIMESTAMP>",
  "entry_type": "exception",
  "risk_tier": "<TIER>",
  "agent_name": "commit",
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
JSON
```

### Step 6: Trigger staging deploy (auto-merge path only)

This step only fires when Step 5 took the auto-merge path AND the merge completed within the polling window. The queue route exits at Step 5 Route B and the merge fire-back loop owns merge + deploy from there (see below).

Read `DEPLOYMENT.md`:

- Platform auto-deploys on push to main (Cloudflare Pages, Vercel, etc.) → note the staging URL; no command needed.
- Manual deploy required → run the staging deploy command from `DEPLOYMENT.md`.

Confirm:

> Staging deploy triggered — `<staging-url>`. Verify gate (`STANDARDS §9`) emits a verify queue entry on staging-deploy success.

**If `DEPLOYMENT.md` doesn't exist:**

> No DEPLOYMENT.md found — skipping staging deploy. Run the `deploy` skill to set up the deployment config.

**If staging deploy fails:** emit an `exception` queue entry per Step 5 Route C with the deploy logs as an artifact. Don't proceed silently.

---

## Doc updates are inline (no post-merge cleanup phase)

Earlier versions of this skill had a Steps 6-7 post-merge cleanup phase that updated `PLANNING.md`, ran `doc-sync` for `PRODUCT.md`, ran `changelog-generator` for `CHANGELOG.md`, and refreshed `LIFECYCLE.md` — on a separate commit pushed after merge. That phase has been folded into Step 4 (inline updates as part of the same PR). Rationale: atomicity, review surface, PLANNING authority at merge time, no separate post-merge PR clutter. Validated end-to-end on the first implementer-driven PR (puzzle-pop PR #6, M0.1, 2026-05-20).

This means `changelog-generator` and `doc-sync` are no longer fired as separate skills in the post-merge phase — their work is composed inline by the agent during commit. They remain available as standalone skills for ad-hoc invocation if needed, but the commit flow doesn't call them.

---

## Merge fire-back loop (out-of-process)

When Step 5 takes the queue route, the skill emits the `approval` packet and exits. The PR sits on GitHub with the `commit-pending-merge` label, ready for Seth's queue approval. The merge fire-back loop (a `*/15 * * * *` Cloudflare Cron Trigger in the hub Worker) bridges "Seth approves the queue entry" → "GitHub merges the PR" via two passes each tick:

**Pass 1 — queue-entry-driven (primary path).** Queries D1 for `approval` entries at `status=resolved`, `resolution_kind IN ('approved','approved-with-changes')`; fetches the `github-pr` artifact; verifies `commit-pending-merge` label; calls GitHub merge API; removes label on 2xx; emits `exception` entry on 405 (MFB.2); removes label silently on 422 (already merged).

**Pass 2 — label-driven safety net (MFB.1).** Iterates all registered products, calls `GET /repos/:owner/:repo/issues?labels=commit-pending-merge` per repo, skips any PR URL already handled by pass 1. For each remaining stranded PR: reads its `tier:*` + `stage:*` labels, applies the `STANDARDS §9` gating table — if auto-merge eligible, calls `processPr` (same path as pass 1); if not, emits a post-hoc `approval` queue entry so the PR surfaces in the decision queue. The entry-driven path keeps priority; the safety net catches PRs where this skill's Steps 4+5 dropped the routing entirely. See `STANDARDS §11.1` (commit Steps 4+5 brittleness) for root-cause context; MFB.1 is the defense-in-depth layer.

There's no post-merge doc cleanup to run — doc updates landed inline at Step 4.

---

## Error handling

| Failure | Behavior |
| --- | --- |
| No changes to commit | Tell Seth; stop. Don't create an empty branch. |
| Untracked files looking unintentional | List them; ask before `git add -A`. |
| Already on a feature branch | Confirm before branching again. |
| Push conflict | `git pull --rebase origin <branch>` then re-push. |
| `gh` not installed | Construct the PR URL from `git remote get-url origin` and present as a clickable link. Auto-merge route falls back to "queue for approval" (no auto-merge possible without `gh` or GitHub API access). |
| Auto-merge 3-min timeout | Skill exits cleanly; merge fire-back loop merges when CI eventually completes. No post-merge resume needed — doc updates already landed inline at Step 4. |
| `LIFECYCLE.md` missing or `stage:` unparseable | Default to `stage: production`; add "LIFECYCLE.md repair needed" to PR body's `## Notes`. |
| Adversary loop doesn't converge within a reasonable cap | Emit `exception` queue entry per Step 5 Route C; no PR opened. (Cap is gap G14 — apply judgment.) |
| CI red on a would-be-auto-merge diff | Flip to MEDIUM queue entry per Step 5; name the failing check in `attempts`. |
| Hub queue API non-2xx | Fall back to legacy in-chat approval; warn that queue write was skipped. |
| `QUEUE_SERVICE_ROLE_KEY` missing | Same fallback; tell Seth to set the secret. |
| Inline doc-update at Step 4 fails (PLANNING / LIFECYCLE / CHANGELOG / PRODUCT) | Stop before opening PR; emit `exception` queue entry naming the failed doc, with the partial branch state preserved. Re-running the skill on the same branch will re-attempt cleanly. |

---

## What this skill does NOT do

- **Run `security-review`.** It runs in CI per `STANDARDS.md §17`; findings appear in the PR body via the central workflow's composer. This skill leaves the sentinel comments in place.
- **Update `ARCHITECTURE.md`.** Out of scope (gap G13 in `STANDARDS.md §11.1`). Flag material architecture changes in `## Notes` for human follow-up.
- **Write `SESSION.md` / `SESSIONS_LOG.md` / `.commit-state`.** All retired (G11). State lives in git, `LIFECYCLE.md`, and the hub's queue.
- **Wait synchronously on queue approval.** The skill exits at Step 5 Route B; the merge fire-back loop resumes it.
- **Deploy to production.** That's `deploy`'s territory.
- **Resolve product slug → hub product_id for the queue packet.** v0 omits `product_id` and uses the title prefix; the hub-side resolution is tracked as a §11.1 follow-up.
