---
name: commit
description: "Use this skill when the implementer agent in CI has a completed work item ready to ship — classify the diff's risk tier, fold canonical-doc updates into the same commit, open the PR, and route to auto-merge, hub-queue approval, or exception. Invoked by the implementer's Step 6 inside the autonomous loop; CI-only. NOT for manual or interactive commits — for those use `simple-commit` or work directly in Claude Code."
canonical_source: fleet-command-center/skills/commit/SKILL.md
---

# Commit Skill

The gate between work and version history. CI-only: invoked by the implementer agent (its Step 6) when a work item's changes are complete and tests have run. There is no interactive mode — the skill never asks a real-time question; all human interaction happens via the hub's decision queue per `STANDARDS.md §7`.

Wraps the diff in a PR, classifies its risk, and routes to one of three outcomes:

- **Auto-merge** (pre-prod stages, or low-tier in beta, or low-tier in production) — apply the `auto-merge` label and exit; the hub merges when CI goes green.
- **Queue for approval** at the hub (medium/high-tier post-prod, or must-escalate match).
- **Block** with an exception queue entry (adversary non-convergence, doc-update failure, etc.).

References: `STANDARDS.md §7` (queue spec), `§8` (escalation packet), `§9` (risk matrix + gating table + must-escalate + exit contract), `§17` (fleet CI centralization), `COMMIT_REDESIGN.md` (original design spec). Packet and activity-record templates: `.fleet-ci/.github/scripts/commit/PACKETS.md` — SKILL.md names variants, PACKETS.md holds the JSON.

---

## Inputs

From the calling implementer:

- The completed diff on the working tree (uncommitted, on `main` of a fresh checkout).
- The work item ID (`M<n>.<x>` or `BL-<n>`) — drives the PR body's closing line and Step 4b's checkbox/backlog flip.
- `<ADVERSARY_CONVERGED>` / `<ADVERSARY_SUMMARY>` — the adversary loop result. The implementer owns and has already run the loop (its Step 5); this skill never re-runs it.
- `<TESTS_SUMMARY>` — test results from the implementer's Step 4, or `none`.
- `$IMPLEMENTER_RUN_ID` env var — stitches this skill's four activity records into the implementer's run bracket.

From the environment:

- `git` and `gh` available and authenticated (CI runner standard).
- `/docs/LIFECYCLE.md` with a `stage:` header per `STANDARDS.md §4`.
- `$QUEUE_SERVICE_ROLE_KEY` — queue + activity writes. Hub URL defaults to `https://sethgibson.com`; override with `HUB_BASE_URL`.

## Exit contract (per `STANDARDS §9`)

This skill terminates with exactly one `exit_reason`, reported back to the calling implementer (which logs it in its run-completed record and tick-report):

| `exit_reason`          | Meaning                                                               |
| ---------------------- | --------------------------------------------------------------------- |
| `auto_merge_initiated` | Route A: PR open + `auto-merge` label applied; hub merges on CI green |
| `queued`               | Route B: PR open + approval packet emitted (or label-sweep fallback)  |
| `exception_emitted`    | Route C: no PR; exception packet emitted                              |

Alongside the exit reason, report `<PR_URL>` and `queue_entry_id` (when any queue entry was emitted) — the implementer forwards both.

It also writes four mid-flight activity records — `commit-started`, `tier-classified`, `pr-opened`, `commit-exited` — at the points marked in the steps below. Templates in `PACKETS.md` § Activity records. Activity writes are observability, not gates: on failure, log `::warning::` and continue.

---

## Steps

> Write the **`commit-started`** activity record on entry.

### Step 1: Inspect

```bash
git status
git diff --stat
git diff main...HEAD 2>/dev/null || git diff HEAD
```

Read the diff carefully — you'll use it for the branch name, commit message, tier classification, and PR body.

- **No changes staged or unstaged** → nothing to commit. This is a caller error (the implementer claimed work exists). Emit the **`exception`** packet per `PACKETS.md` with cause "no diff to commit" and exit `exception_emitted`.
- **Untracked files present** → they're part of the implementer's work product on a fresh CI checkout; include them. If something looks anomalous (build artifacts, secrets-shaped filenames), exclude it, note the exclusion in the PR body's `## Notes`, and let the must-escalate / tier classification catch anything sensitive.

Also read up front (cached for later steps):

```bash
head -10 docs/LIFECYCLE.md 2>/dev/null || head -10 LIFECYCLE.md
```

Extract `stage:` and `monetized:` from the frontmatter header. Missing or unparseable → default to `stage: production`, `monetized: true` (fail up). Note this in the PR body as "LIFECYCLE.md repair needed."

**Migration detection (per `STANDARDS §9` "Database-migration handling").** While reading the diff, check whether it adds schema-migration files: new `*.sql` under the Drizzle `out` directory (read `drizzle.config.*`; default `migrations/` or `drizzle/`), or the equivalent for the product's ORM. If yes, also check whether the product's deploy is **migration-aware**: its deploy workflow (`.github/workflows/*`) or `DEPLOYMENT.md`-documented deploy command contains a `migrations apply` step. Record `<MIGRATIONS_DETECTED>` (`true`/`false`) and `<DEPLOY_MIGRATION_AWARE>` (`true`/`false`/`n/a` when no migrations detected) for the Step 4f bundle; they drive Step 5.6.

### Step 2: Consume the adversary result

The implementer already ran the adversary loop (its Step 5) before invoking this skill. Do NOT re-run `pre-commit-reviewer`. Consume:

- `<ADVERSARY_CONVERGED>` — `false` means the loop cap-hit; the implementer normally exits before reaching this skill in that case, but if it arrives here, it routes to Route C at Step 5.
- `<ADVERSARY_SUMMARY>` — lands in the PR body's `## Adversary review` section and the approval packet.

> **Note:** `security-review` runs in CI (per `STANDARDS.md §17` — central workflow at `Aethrix-Labs/.github`). Don't invoke it here. Its findings appear in the PR body via the security-review composer's sentinel (Step 4).

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

> Write the **`tier-classified`** activity record.

### Step 4: Branch, update canonical docs inline, commit, push, open PR

> **Step 4 must complete before Step 5 starts.** Step 5 reads Step 4's output bundle. Do not skip ahead — Step 5's first action is a guard that fails loudly if the bundle is missing.

**4a. Branch.**

```bash
git checkout -b <branch-name>
```

**Branch name:** kebab-case, short and descriptive. Examples: `add-google-sso`, `fix-auth-redirect`, `dashboard-layout`.

In CI the working tree is always a fresh checkout of `main`. If HEAD is somehow not `main`, something upstream is broken — emit the **`exception`** packet with cause "unexpected HEAD at commit time" and exit `exception_emitted`.

**4b. Update the canonical docs inline as part of this same commit.** Doc updates that describe what just shipped MUST land in the same PR as the code change — atomicity (code and docs revert together), review surface, and PLANNING-authority-at-merge-time all favor inline. (`changelog-generator` and `doc-sync` are not invoked; their work is composed inline here.)

The docs to consider, in order:

- **`PLANNING.md`** — flip the completed step's checkbox to `[x]`. If multiple sub-bullets were the step's acceptance criteria, flip those too. Be conservative — only check off what was actually fully completed by this PR. Partial → leave unchecked and add `*(partial — X done)*` in the step body.
  - **Milestone-close check.** After flipping, test whether the step was **milestone-closing** per `STANDARDS §9`: every top-level checkbox under its parent `## M<n>` heading is now `[x]`. If yes, read `.fleet-ci/.github/scripts/milestone-close/SKILL.md` and run its **Phase 1** (archive the closed milestone to `PLANNING_ARCHIVE.md` via `compact.py --milestone`) so the archive rides in this same commit. Record `<MILESTONE_CLOSED> = <M-id>` for the Step 4f bundle; record `false` when no milestone closed. Run Phase 1 **after** this PLANNING flip and **before** the `LIFECYCLE.md` update below (so `next_milestone:` reads from the post-archive file).
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

> Write the **`pr-opened`** activity record.

**4e. Capture CI status.**

```bash
gh pr checks <PR_NUMBER> --json bucket,name,state
```

Reduce to a single value: `failed` if any required check has bucket `fail`; otherwise `pending` if any check is still running; otherwise `pass`. Record as `<CI_STATUS>`. One read only — never re-poll.

**4f. Emit the Step 4 output bundle** (Step 5 reads these by name; do not proceed without them):

| Name                    | Source                              | Example                                  |
| ----------------------- | ----------------------------------- | ---------------------------------------- |
| `<TIER>`                | Step 3 output                       | `low`                                    |
| `<RATIONALE>`           | Step 3 output                       | `matrix row: code — non-functional`      |
| `<STAGE>`               | `LIFECYCLE.md`                      | `in-development`                         |
| `<MUST_ESCALATE_HIT>`   | Step 3a result                      | `false`                                  |
| `<ADVERSARY_CONVERGED>` | Step 2 (from implementer)           | `true`                                   |
| `<CI_STATUS>`           | Step 4e                             | `pending`                                |
| `<PR_NUMBER>`           | Step 4d return                      | `42`                                     |
| `<PR_URL>`              | Step 4d return                      | `https://github.com/.../pull/42`         |
| `<PR_TITLE>`            | Step 4c commit summary              | `feat: add SSO redirect`                 |
| `<PRODUCT_SLUG>`        | repo name or `LIFECYCLE.md`         | `puzzle-pop`                             |
| `<PLANNING_ANCHOR_URL>` | derived from `PLANNING.md` step ref | `https://github.com/.../PLANNING.md#m07` |
| `<ADVERSARY_SUMMARY>`   | Step 2 (from implementer)           | `converged, no findings`                 |
| `<TESTS_SUMMARY>`       | implementer input or `none`         | `3 added, 0 untestable`                  |
| `<RECOMMENDATION>`      | composed from Step 2 + 4e           | `Approve — all checks green`             |
| `<MILESTONE_CLOSED>`    | Step 4b milestone-close check       | `M3` or `false`                          |
| `<MIGRATIONS_DETECTED>` | Step 1 migration detection          | `true`                                   |
| `<DEPLOY_MIGRATION_AWARE>` | Step 1 awareness check           | `false` (or `n/a`)                       |
| `<MIGRATION_FILES>`     | Step 1 migration detection          | `migrations/0007_learning_loop.sql`      |

**If `<ADVERSARY_CONVERGED> == false`,** Steps 4a–4f are skipped entirely (no PR is opened on Route C). The bundle then only requires `<TIER>`, `<RATIONALE>`, `<STAGE>`, `<MUST_ESCALATE_HIT>`, `<ADVERSARY_CONVERGED>`, `<PRODUCT_SLUG>`; the PR-related fields are absent by design. Step 5's guard accounts for this.

### PR body composition (per `COMMIT_REDESIGN.md §8`)

Fixed section order, conditional inclusion. Plain Markdown.

```markdown
## Summary

<One paragraph: what this PR does and why. Include "Closes step M<n>.<x>" or "Closes backlog item BL-<n>".>

## PLANNING.md step

- [x] M<n>.<step> — <step text>
      <Link to the PLANNING.md anchor. Omit section for backlog-item commits.>

## Risk tier

**<tier>** — <rationale string from Step 3>

## Adversary review

<ADVERSARY_SUMMARY + any caveats carried from the implementer's loop. Omit if returned clean.>

## Security review

<!-- security-review:start --><!-- security-review:end -->

<Sentinel for the central security-review workflow's PR body composer per STANDARDS.md §17. The composer fills this in post-CI; leave the empty sentinels here.>

## Tests

<TESTS_SUMMARY: tests added, coverage delta, untestable gaps. Omit if no tests ran.>

## Rollback

<Concrete revert instructions. Required for medium/high tier. Omit for low.>

## Notes

<Anything Seth would want to know. Optional.>
```

**Truncation order if the PR body exceeds GitHub's size cap:** `Notes` → `Adversary review` → `Tests`. Never truncate `Summary`, `Risk tier`, `Rollback`.

### Step 5: Gate — auto-merge, queue, or block

This step is **mechanical**. Walk the decision tree top-down to a single terminal route, then run that route's pre-written actions, substituting placeholders only. Do not compose new shell commands. Do not re-interpret the §9 table — the tree below is the §9 table, pre-walked.

**5a. Guard — verify Step 4 outputs exist.**

- **Always required** (every run): `<TIER>`, `<RATIONALE>`, `<STAGE>`, `<MUST_ESCALATE_HIT>`, `<ADVERSARY_CONVERGED>`, `<PRODUCT_SLUG>`, `<MIGRATIONS_DETECTED>`, `<DEPLOY_MIGRATION_AWARE>`.
- **Additionally required when `<ADVERSARY_CONVERGED> == true`** (a PR exists): `<CI_STATUS>`, `<PR_NUMBER>`, `<PR_URL>`, `<PR_TITLE>`, `<PLANNING_ANCHOR_URL>`, `<ADVERSARY_SUMMARY>`, `<TESTS_SUMMARY>`, `<RECOMMENDATION>`, `<MILESTONE_CLOSED>`.

If any required name is missing, **stop immediately**: log `::error::commit: Step 5 guard failed — Step 4 outputs missing: <list>`, emit the **`exception`** packet with that cause, and exit `exception_emitted`. Do not pick a route. Missing outputs mean Step 4 didn't finish; pressing on produces exactly the brittleness the §11.3 entry resolves.

**5b. Decision tree — terminate on the first matching branch.**

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

**5c. Run the route's actions.**

---

**Route A — auto-merge.**

```bash
gh pr edit <PR_NUMBER> --add-label "auto-merge"
```

That's the whole route. Do NOT call `gh pr merge`, and do NOT poll merge state — merge execution belongs to the hub: when CI goes green, the central security-review workflow's `merge-ready` job pings the hub, which runs `mergePr()` and advances the orchestrator. Exit reason: **`auto_merge_initiated`**.

---

**Route B — queue for approval.**

Emit the **`approval`** packet per `PACKETS.md`. Exit reason: **`queued`**, with `queue_entry_id` from the response.

**On non-2xx or `QUEUE_SERVICE_ROLE_KEY` missing:** log `::warning::commit: queue write failed (<reason>); PR #<PR_NUMBER> left with commit-pending-merge label for the hourly label sweep (MFB.1)`. The PR stays open and labeled; the hub's label-driven safety net surfaces it in the decision queue. Still exit **`queued`** (with `queue_entry_id: null`).

---

**Route C — block (exception).**

Emit the **`exception`** packet per `PACKETS.md`. No PR is opened; the diff stays on the local branch (pushed to the remote branch if 4c completed, otherwise local-only — name the branch in the packet either way). Exit reason: **`exception_emitted`**.

### Step 5.5: Milestone follow-up emission (conditional — Routes A and B only)

Fires iff `<MILESTONE_CLOSED> != false` and a PR was opened (Routes A and B; never Route C). Runs **before** Step 6 on Route A, and as the final action before exit on Route B.

Read `.fleet-ci/.github/scripts/milestone-close/SKILL.md` and run its **Phase 2**: compose the manual test plan and POST the medium-tier `follow-up` queue entry (shipped summary + how Seth should manually test the milestone). The entry is non-blocking — on POST failure or missing `QUEUE_SERVICE_ROLE_KEY`, log a warning, include the test plan in this skill's final output, and continue. Never block or unwind the merge over it.

### Step 5.6: Migration-pending emission (conditional — Routes A and B only)

Fires iff `<MIGRATIONS_DETECTED> == true` AND `<DEPLOY_MIGRATION_AWARE> == false` and a PR was opened (Routes A and B; never Route C). Per `STANDARDS §9` "Database-migration handling."

Emit the **`migration-pending`** packet per `PACKETS.md`: migration filenames, DB binding, the exact `wrangler d1 migrations apply` commands for staging and prod, and the PR URL. The PR's merge route is unaffected — the code is fine; the entry exists so the hub blocks the *next dispatch* until migrations are applied and the entry is resolved (hub-side enforcement, same point as verify entries).

Unlike Step 5.5, a failed POST here is NOT silently absorbed: on non-2xx or missing `QUEUE_SERVICE_ROLE_KEY`, log `::error::commit: migration-pending entry failed to post — migrations in PR #<PR_NUMBER> will NOT block dispatch; apply manually: <commands>` and surface the apply commands in the skill's final output. Still exit on the route's normal exit reason. When `<DEPLOY_MIGRATION_AWARE> == true`, skip — CD applies the migrations on deploy; a deploy failure routes through the existing exception path.

### Step 6: Exit

> Write the **`commit-exited`** activity record.

Report the terminal `exit_reason`, `<PR_URL>` (or null), and `queue_entry_id` (or null) back to the calling implementer, then stop. Do not wait for merge, CI, deploy, or queue resolution — all out-of-process per `STANDARDS §9` "Commit-skill exit contract."

---

## Out-of-process merge (context, not instructions)

Merge execution lives in the hub Worker (`mergePr()`, holding `GITHUB_MERGE_TOKEN`), reached by two event-driven paths: **path A** — CI green on an `auto-merge`-labeled PR → the central workflow's `merge-ready` ping → `mergePr()`; **path B** — Seth approves the queue entry → the approval handler calls `mergePr()` synchronously. An hourly label-driven sweep (MFB.1) reconciles any PR stranded with `commit-pending-merge` (e.g. a dropped queue write). Post-merge deploy is CD on push to main; the verify gate emits a verify queue entry on staging-deploy success.

---

## Error handling

| Failure                                                | Behavior                                                                                                                                                                       |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| No changes to commit                                   | Exception packet ("no diff to commit"); exit `exception_emitted`.                                                                                                              |
| Anomalous untracked files                              | Exclude; note in PR body `## Notes`; continue.                                                                                                                                 |
| HEAD not on `main` at Step 4a                          | Exception packet ("unexpected HEAD at commit time"); exit `exception_emitted`.                                                                                                 |
| Push conflict                                          | `git pull --rebase origin <branch>` then re-push once; still failing → exception packet.                                                                                       |
| `LIFECYCLE.md` missing or `stage:` unparseable         | Default to `stage: production`, `monetized: true`; add "LIFECYCLE.md repair needed" to PR body's `## Notes`.                                                                   |
| `<ADVERSARY_CONVERGED> == false` reaches this skill    | Route C; exit `exception_emitted`. (The implementer normally cap-hit-exits before invoking commit.)                                                                            |
| CI red on a would-be-auto-merge diff                   | Decision tree branch 3 → Route B at MEDIUM; name the failing check in `attempts`.                                                                                              |
| Queue write non-2xx / `QUEUE_SERVICE_ROLE_KEY` missing | Route B: leave PR + `commit-pending-merge` label for the MFB.1 sweep; warn; exit `queued`. Route C: log `::error::` (the block is unreported — the workflow log is the trail). |
| Activity write fails                                   | Log `::warning::`; continue. Observability, not a gate.                                                                                                                        |
| Inline doc-update at Step 4b fails                     | Stop before opening PR; exception packet naming the failed doc, partial branch state preserved. Re-running on the same branch re-attempts cleanly.                             |
| Milestone archive (Step 4b milestone-close Phase 1) fails or script missing | Per `milestone-close/SKILL.md` Phase 1c: "not archivable" → recheck closure, don't force; "not found" with the milestone already in `PLANNING_ARCHIVE.md` → idempotent success; script missing / §9 contract mismatch → skip archive, note in PR body `## Notes`, still run Step 5.5. |
| Milestone follow-up POST (Step 5.5) non-2xx or key missing | Log `::warning::`, surface the test plan in the skill's final output, continue. Non-blocking by design. |
| Migration-pending POST (Step 5.6) non-2xx or key missing | Log `::error::` with the apply commands inline (loud — an unposted entry means dispatch won't block); surface commands in final output; continue on the route's normal exit reason. |

---

## What this skill does NOT do

- **Run the adversary loop.** The implementer owns it (its Step 5); this skill consumes the result.
- **Run `security-review`.** It runs in CI per `STANDARDS.md §17`; findings land in the PR body via the central workflow's composer. This skill leaves the sentinel comments in place.
- **Merge PRs or poll merge state.** Merge execution is the hub's `mergePr()`, triggered by the `merge-ready` ping (path A) or Seth's approval (path B).
- **Trigger deploys.** Post-merge deploy is CD on push to main; production deploys are the `deploy` skill's territory.
- **Interact in chat.** CI-only. Manual commits go through `simple-commit` or Claude Code directly.
- **Update `ARCHITECTURE.md`.** Out of scope (gap G13 in `STANDARDS.md §11.1`). Flag material architecture changes in `## Notes` for human follow-up.
- **Wait synchronously on queue approval, CI, or merge.** The skill exits at its terminal route; the hub owns everything after.
