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

### Step 4: Branch, commit, push, open PR

```bash
git checkout -b <branch-name>
git add -A
git commit -m "<type>: <short imperative summary>

<One or two sentences explaining what and why, if non-obvious.>"
git push -u origin <branch-name>
```

**Branch name:** kebab-case, short and descriptive. Examples: `add-google-sso`, `fix-auth-redirect`, `dashboard-layout`.

**If already on a feature branch** (not `main`): confirm with Seth before creating another branch on top.

**Conventional commit style:** check `git log --oneline -5`. If the repo uses `feat:`/`fix:`/`chore:`/etc., follow that. Otherwise plain imperative summary.

**Open the PR:**

```bash
gh pr create --base main \
  --title "<commit message summary>" \
  --body "$(compose_pr_body)" \
  --label "tier:<tier>,stage:<stage>,commit-pending-merge$(auto_merge_label)"
```

PR labels (applied at open per `COMMIT_REDESIGN.md §7.1`):

- `tier:low` / `tier:medium` / `tier:high` — from Step 3.
- `stage:<value>` — mirrored from `LIFECYCLE.md`.
- `commit-pending-merge` — always; the merge poller queries on this.
- `auto-merge` — only when Step 5 says auto-merge is allowed.

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

Apply the (tier × stage) gating table from `STANDARDS.md §9`:

| Stage | Auto-merge | Queue for approval |
| --- | --- | --- |
| `idea`, `mvp`, `in-development`, `graduated`, `deprecated` | All tiers | None |
| `beta` | low, medium | high |
| `production` | low | medium, high |

**Overrides (in order):**

1. **CI red** → flip to MEDIUM queue entry. Never auto-merge red CI. Name the failing check in the packet.
2. **Must-escalate match** → always queue at HIGH regardless of stage.
3. **Adversary loop exception** → exception queue entry (different type), no merge attempt.

**Route A — auto-merge:**

```bash
gh pr merge <pr-number> --auto --squash
```

The `auto-merge` label tells branch protection to fire on green CI. Poll merge state every ~30s up to ~3 min:

```bash
gh pr view <pr-number> --json state,mergedAt
```

- Merged → proceed to Step 6.
- 3-min timeout → tell Seth CI is slow, present the PR link, offer to keep waiting or pause. Skill exits cleanly; the merge poller (fleet-level cron, see "Merge fire-back" below) will pick it up later.

**Route B — queue for approval:**

POST an `approval` entry to the hub's queue and exit. The skill does NOT wait. The merge fire-back loop (out-of-process, see "Merge fire-back" below) resumes Step 6 when the entry is approved and merged.

```bash
curl -X POST "${HUB_BASE_URL:-https://sethgibson.com}/api/v1/queue/entries" \
  -H "Content-Type: application/json" \
  -H "x-service-role-key: ${QUEUE_SERVICE_ROLE_KEY}" \
  -d "$(compose_queue_packet)"
```

Packet shape (matches `STANDARDS.md §8` + the M3 API contract):

```json
{
  "request_id": "<sha256 of pr_url — idempotent on re-run>",
  "entry_type": "approval",
  "risk_tier": "<low|medium|high>",
  "agent_name": "commit",
  "title": "[<product-slug>] Ship PR #<n>: <PR title>",
  "goal": "Ship PR #<n>: <PR title>",
  "attempts": [
    "Adversary loop: <converged | findings noted in PR>",
    "CI: <green | red — <failing check>>",
    "Tests: <count added | none>"
  ],
  "ask": "Approve this PR for merge?",
  "recommendation": "Approve — all checks green, no escalations. | <specifics if not>",
  "artifacts": [
    { "label": "PR", "href": "<pr-url>", "artifact_type": "github-pr" },
    { "label": "PLANNING step", "href": "<planning-anchor-url>", "artifact_type": "planning-step" }
  ]
}
```

**Note on `product_id`:** the API expects a hub-side `products.id` (UUID), not a slug. v0 omits the field and encodes the product slug in `title` (the `[<slug>]` prefix above). A `§11.1` follow-up tracks adding slug→ID resolution at the hub so future packets can populate it properly.

**On 2xx:** queue write succeeded. Tell Seth (or the calling agent):

> PR #<n> opened and queued for approval — `<queue-entry-url>`. Skill exiting; resume runs after merge.

**On non-2xx, or `QUEUE_SERVICE_ROLE_KEY` missing:** fall back to legacy behavior — leave the PR open, tell Seth approval is needed in-chat, do not auto-merge:

> PR #<n> needs your approval (stage: <stage>, tier: <tier>). **[Review on GitHub](<pr-url>)**
> Queue write failed (<reason>) — let me know when you've merged it and I'll run post-merge cleanup.

**Route C — block (exception):**

For adversary-loop non-convergence or other hard failures, emit an `exception` entry instead of `approval`:

```json
{
  "request_id": "<sha256 of branch-name + timestamp>",
  "entry_type": "exception",
  "risk_tier": "<from Step 3>",
  "agent_name": "commit",
  "title": "[<product-slug>] commit blocked: <one-line cause>",
  "goal": "<what the agent was trying to do>",
  "attempts": [...],
  "ask": "<what Seth needs to decide / clarify>",
  "recommendation": "<optional>",
  "artifacts": [ ... branch link, diff link, adversary report link ... ]
}
```

The PR is NOT opened in this case. The diff stays on the local branch.

### Step 6: Resume on merge (post-merge cleanup)

Triggered either by:

- Direct continuation when Step 5 took the auto-merge path and the merge fired during the polling window.
- The merge fire-back poller invoking the skill in "resume mode" with the merged PR's number, when Step 5 took the queue route or the auto-merge polling window expired.

```bash
git checkout main
git pull origin main
```

Then update the four canonical docs (each is a separate concern; failures on any one are recoverable, not fatal):

**6a. PLANNING.md.** Read it; mark steps completed by this PR as done. Use the diff to identify what was done. Be conservative — only check off items you're confident were fully completed. Partial → leave unchecked and add `*(partial — X done)*`.

```
- [ ] M3.5 — Foo  →  - [x] M3.5 — Foo
```

If nothing clearly matches, leave PLANNING.md unchanged.

**6b. Run `doc-sync` skill.** Per `SKILL_AUDIT.md §3.9`, scope is user-facing `PRODUCT.md` content (in-app FAQ, help docs, agent-readable product knowledge). `ARCHITECTURE.md` is NOT in scope for `doc-sync` (gap G13 in `STANDARDS.md §11.1` — ownership TBD). If the diff materially changes architecture, flag it in `## Notes` of the PR body for human follow-up.

**6c. Run `changelog-generator` skill.** Prepends a new entry to `CHANGELOG.md` with user-facing and developer-facing sections.

**6d. Refresh LIFECYCLE.md.** Always update `last_updated:` to today's date. Update `next_milestone:` if a milestone just completed. Body sections only if state materially shifted.

### Step 7: Commit and push post-merge cleanup

```bash
git add docs/PLANNING.md docs/PRODUCT.md docs/CHANGELOG.md docs/LIFECYCLE.md
# (adjust paths if any of these live at repo root rather than /docs/)
git commit -m "chore: post-merge cleanup — <one-line summary of what shipped>"
git push
```

Confirm:

> Done! Back on `main`, docs updated, `LIFECYCLE.md` refreshed, everything pushed.

### Step 8: Trigger staging deploy

Read `DEPLOYMENT.md`:

- Platform auto-deploys on push to main (Cloudflare Pages, Vercel, etc.) → note the staging URL; no command needed.
- Manual deploy required → run the staging deploy command from `DEPLOYMENT.md`.

Confirm:

> Staging deploy triggered — `<staging-url>`. Let me know when you've verified staging and want to promote to production.

**If `DEPLOYMENT.md` doesn't exist:**

> No DEPLOYMENT.md found — skipping staging deploy. Run the `deploy` skill to set up the deployment config.

**If staging deploy fails:** emit an `exception` queue entry per Step 5 Route C with the deploy logs as an artifact. Don't proceed silently.

---

## Merge fire-back (out-of-process)

The skill emits the queue entry and exits at Step 5 Route B. The post-merge resume (Steps 6–8) is triggered by an out-of-process loop:

- **v0:** Fleet-level scheduled task fires every ~15 min, queries GitHub for PRs with label `commit-pending-merge` across `~/products/*`, and on each merged PR invokes a Claude Code subagent in that repo to run Steps 6–8 in "resume mode."
- **v1:** GitHub webhook → hub endpoint → invoke resume subagent. Lower latency; built when webhook infra exists.

Implementation of the poller / webhook is out of scope for this SKILL.md — it's a separate piece of fleet infrastructure tracked in `STANDARDS.md §11.2`. The skill is forward-compatible: it tags PRs with the right labels and emits the right queue packets, so the poller can find them when it ships.

---

## Error handling

| Failure | Behavior |
| --- | --- |
| No changes to commit | Tell Seth; stop. Don't create an empty branch. |
| Untracked files looking unintentional | List them; ask before `git add -A`. |
| Already on a feature branch | Confirm before branching again. |
| Push conflict | `git pull --rebase origin <branch>` then re-push. |
| `gh` not installed | Construct the PR URL from `git remote get-url origin` and present as a clickable link. Auto-merge route falls back to "queue for approval" (no auto-merge possible without `gh` or GitHub API access). |
| Auto-merge 3-min timeout | Skill exits cleanly; merge poller picks up the post-merge resume when CI eventually completes. |
| `LIFECYCLE.md` missing or `stage:` unparseable | Default to `stage: production`; add "LIFECYCLE.md repair needed" to PR body's `## Notes`. |
| Adversary loop doesn't converge within a reasonable cap | Emit `exception` queue entry per Step 5 Route C; no PR opened. (Cap is gap G14 — apply judgment.) |
| CI red on a would-be-auto-merge diff | Flip to MEDIUM queue entry per Step 5; name the failing check in `attempts`. |
| Hub queue API non-2xx | Fall back to legacy in-chat approval; warn that queue write was skipped. |
| `QUEUE_SERVICE_ROLE_KEY` missing | Same fallback; tell Seth to set the secret. |
| Post-merge step (6–8) failure | Emit `exception` queue entry per failed step; partial progress preserved. Re-running the skill in the same repo state will re-attempt cleanly. |

---

## What this skill does NOT do

- **Run `security-review`.** It runs in CI per `STANDARDS.md §17`; findings appear in the PR body via the central workflow's composer. This skill leaves the sentinel comments in place.
- **Update `ARCHITECTURE.md`.** Out of scope (gap G13 in `STANDARDS.md §11.1`). Flag material architecture changes in `## Notes` for human follow-up.
- **Write `SESSION.md` / `SESSIONS_LOG.md` / `.commit-state`.** All retired (G11). State lives in git, `LIFECYCLE.md`, and the hub's queue.
- **Wait synchronously on queue approval.** The skill exits at Step 5 Route B; the merge fire-back loop resumes it.
- **Deploy to production.** That's `deploy`'s territory.
- **Resolve product slug → hub product_id for the queue packet.** v0 omits `product_id` and uses the title prefix; the hub-side resolution is tracked as a §11.1 follow-up.
