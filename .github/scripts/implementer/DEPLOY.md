# Implementer Skill — Deployment Guide

This doc accompanies `/skills/implementer/SKILL.md`. It captures the **deployment shape** for the implementer's CI surface: the central reusable workflow that lives at `Aethrix-Labs/.github` and the thin per-product stub that opts each product into the autonomous loop.

Per `STANDARDS.md §17.4`, workflow YAML doesn't live in `/skills/`. The YAMLs below are documentation for the deployment, not the deployed files themselves — Seth applies them to the respective deployment locations manually (matches the security-review pattern).

---

## Architecture

```
                     ┌──────────────────────────────────────────────────┐
                     │  Seth (or future wake mechanism)                 │
                     │  clicks "Run workflow" on product's              │
                     │  Actions UI, or hub fires workflow_dispatch      │
                     └────────────────────┬─────────────────────────────┘
                                          │ workflow_dispatch
                                          │ inputs: { action, verify_entry_id }
                                          ▼
              ┌───────────────────────────────────────────────────────┐
              │  <product-repo>:.github/workflows/implementer.yml     │
              │  (~15-line stub, per-product)                         │
              │  Calls central reusable workflow with `secrets: inherit` │
              └───────────────────────────┬───────────────────────────┘
                                          │ jobs.<job>.uses
                                          ▼
              ┌───────────────────────────────────────────────────────┐
              │  Aethrix-Labs/.github:.github/workflows/              │
              │     implementer-callable.yml                          │
              │  (does the actual work)                               │
              │  - Checks out consumer repo + .fleet-ci/              │
              │  - Invokes claude-code-action with the SKILL.md       │
              │    prompt                                             │
              │  - QUEUE_SERVICE_ROLE_KEY passed for commit's queue   │
              │    emission                                           │
              └───────────────────────────┬───────────────────────────┘
                                          │ Read .fleet-ci/.github/scripts/implementer/SKILL.md
                                          │ Read .fleet-ci/.github/scripts/commit/SKILL.md
                                          ▼
                                ┌─────────────────────┐
                                │  Claude agent does  │
                                │  one PLANNING step  │
                                │  + invokes commit   │
                                └─────────────────────┘
```

---

## Central reusable workflow

**Target:** `Aethrix-Labs/.github:.github/workflows/implementer-callable.yml`

```yaml
# implementer-callable.yml — central reusable workflow for the implementer agent.
#
# Deployed at:   Aethrix-Labs/.github:.github/workflows/implementer-callable.yml
# Convention:    STANDARDS.md §9 (per-step verification gate), §17 (Fleet CI)
# Skill source:  sethgibson-com:/skills/implementer/SKILL.md
#                → promoted to Aethrix-Labs/.github:.github/scripts/implementer/SKILL.md
#                  via the §17.5 drift/update protocol
# Caller:        <product-repo>:.github/workflows/implementer.yml (per-product stub)
#
# This workflow is `workflow_call`-able (reusable). Each product's stub
# triggers it. We use this shape rather than `org-required-workflow` because
# the implementer is an *opt-in* per product (not every product wants
# autonomous operation — Fleet Command Center explicitly doesn't).

name: implementer (callable)

on:
  workflow_call:
    inputs:
      action:
        description: "next-step | address-feedback"
        type: string
        required: false
        default: "next-step"
      verify_entry_id:
        description: "Optional verify queue entry ID from the wake mechanism"
        type: string
        required: false
        default: ""
      product_slug:
        description: "Product slug (defaults to repo basename)"
        type: string
        required: false
        default: ""

permissions:
  contents: write          # commit + push from the implementer agent
  pull-requests: write     # gh pr create / merge
  id-token: write          # required by anthropics/claude-code-action@v1

concurrency:
  # Serialize per product — never run two implementer invocations on the same
  # repo at once. Default `cancel-in-progress: false` so a queued second run
  # waits rather than killing the in-flight one.
  group: implementer-${{ github.repository }}
  cancel-in-progress: false

jobs:
  implementer:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4    # consumer repo at $GITHUB_WORKSPACE
        with:
          fetch-depth: 0             # needed for full git history (commit skill reads log)

      - uses: actions/checkout@v4    # central repo (SKILL.md + scripts)
        with:
          repository: Aethrix-Labs/.github
          path: .fleet-ci
          ref: main

      - name: Set up gh CLI
        # The commit skill uses `gh pr create`, `gh pr merge --auto`, etc.
        # `gh` is preinstalled on ubuntu-latest runners; nothing to do.
        run: gh --version

      - name: Configure git for commits
        run: |
          git config user.name  "implementer-agent[bot]"
          git config user.email "implementer-agent@aethrix-labs.local"

      - name: Run implementer agent
        uses: anthropics/claude-code-action@v1
        env:
          # Used by the commit skill's queue emission path (per STANDARDS §14
          # multi-storage credentials). Same secret security-review uses.
          QUEUE_SERVICE_ROLE_KEY: ${{ secrets.QUEUE_SERVICE_ROLE_KEY }}
          # Dispatch payload passed through to the agent via env so the
          # SKILL.md prompt can branch on action / verify_entry_id.
          IMPLEMENTER_ACTION: ${{ inputs.action }}
          IMPLEMENTER_VERIFY_ENTRY_ID: ${{ inputs.verify_entry_id }}
          IMPLEMENTER_PRODUCT_SLUG: ${{ inputs.product_slug }}
        with:
          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
          prompt: |
            You are running as the **implementer agent** in CI.

            Read `.fleet-ci/.github/scripts/implementer/SKILL.md` and follow
            it exactly. It defines the pre-flight guards, the flow (read
            PLANNING.md → pick next unblocked step → implement → invoke commit
            → exit), and the failure modes.

            When ready to commit, read `.fleet-ci/.github/scripts/commit/SKILL.md`
            and follow its 8-step flow. The commit skill handles tier
            classification, PR body composition, queue emission, and merge
            routing.

            Runtime context:
            - Working directory has the full consumer repo (fetch-depth: 0).
            - Action: $IMPLEMENTER_ACTION (default "next-step").
            - Verify entry ID: $IMPLEMENTER_VERIFY_ENTRY_ID (empty on manual triggers).
            - Product slug: $IMPLEMENTER_PRODUCT_SLUG (or derive from $GITHUB_REPOSITORY).

            Exit after ONE step. The verify gate is the loop boundary.
            Do not chain into the next step.
          claude_args: |
            --model claude-sonnet-4-6
            --max-turns 80
            --allowedTools Read,Bash,Write,Edit,Glob,Grep
```

**Notes on this YAML:**

- `--max-turns 80` is intentionally generous. The implementer does open-ended engineering work; the commit skill alone runs ~8 steps with bash invocations. Tune down if cost becomes an issue.
- Model is `claude-sonnet-4-6` per `STANDARDS.md §14`. The implementer is reasoning-heavy (open-ended engineering judgment on every step) so Haiku would underperform. Add a row to §14 "Per-agent assignments" when this lands.
- Tools allow Read/Bash/Write/Edit/Glob/Grep — more than security-review's because the implementer makes code changes, not just reads. No `WebFetch` — the agent shouldn't need external web access for fleet-native products.
- `permissions: contents: write` is required for the agent to push commits. This is broader than security-review's `contents: read`; it's the right scope for an agent that's literally writing code.

---

## Per-product stub workflow

**Target:** `<product-repo>:.github/workflows/implementer.yml` (one file per opted-in product)

```yaml
# implementer.yml — per-product stub that opts this repo into the autonomous loop.
#
# Calls the central reusable workflow at Aethrix-Labs/.github.
# Deployment-only file; canonical reference: sethgibson-com:/skills/implementer/DEPLOY.md.
#
# To remove a product from the autonomous loop: delete this file.

name: implementer

on:
  workflow_dispatch:
    inputs:
      action:
        description: "next-step | address-feedback"
        type: choice
        options: ["next-step", "address-feedback"]
        default: "next-step"
        required: true
      verify_entry_id:
        description: "Verify queue entry ID (leave blank for manual triggers)"
        type: string
        required: false
        default: ""

permissions:
  contents: write          # required so the callee can commit + push
  pull-requests: write     # required so the callee can open + merge PRs
  id-token: write          # required by anthropics/claude-code-action@v1 (OIDC)

jobs:
  implementer:
    uses: Aethrix-Labs/.github/.github/workflows/implementer-callable.yml@main
    secrets: inherit
    with:
      action: ${{ inputs.action }}
      verify_entry_id: ${{ inputs.verify_entry_id }}
```

**Notes:**

- `secrets: inherit` passes through `CLAUDE_CODE_OAUTH_TOKEN` and `QUEUE_SERVICE_ROLE_KEY` from the org-level secrets to the central reusable workflow.
- **`permissions:` at the caller is required, not optional.** GitHub Actions only passes down permissions the caller has explicitly granted; the callee's own `permissions:` block is a *ceiling*, not a grant. Missing this block on the stub manifests as `"is requesting ... write, but is only allowed ... read|none"` at workflow validation time — the first puzzle-pop smoke test hit exactly this (2026-05-20; folded back into this DEPLOY.md as the canonical stub shape). The block must match (or exceed) the central workflow's `permissions:` declaration verbatim.
- **Repo-level Actions settings caveat.** If `<product-repo>` → Settings → Actions → General → "Workflow permissions" is set to "Read repository contents and packages permissions" (default), the block above is sufficient — workflow-level grants up to the configured ceiling work. If it's set to read-only with no bump path, the block won't help; bump the repo (or org) setting first.
- `@main` ref means the product picks up central updates automatically on next dispatch. Pin to a tag if a product needs to freeze.
- The `action: choice` dropdown is for Seth's manual triggers from the Actions UI. The wake mechanism (when built) passes the field programmatically; both surfaces use the same workflow.

---

## Installation order (per product)

1. **Confirm vended dependencies are in place.** The central workflow Reads `.fleet-ci/.github/scripts/implementer/SKILL.md` and `.fleet-ci/.github/scripts/commit/SKILL.md`. Both must be vended to `Aethrix-Labs/.github` before the workflow can run successfully. See `STANDARDS.md §13` and `§17.5` for the promotion protocol.
2. **Add the central reusable workflow** to `Aethrix-Labs/.github` at the path above. First-PR workflow-validation gotcha applies (same one security-review hit).
3. **Add the per-product stub** to the target product's repo at `.github/workflows/implementer.yml`. Merge it via a regular PR.
4. **Confirm org-level secrets exist:** `CLAUDE_CODE_OAUTH_TOKEN` and `QUEUE_SERVICE_ROLE_KEY`. Both already in place from earlier work.
5. **Click "Run workflow"** on the product's Actions UI → `implementer` → use default `action: next-step` → Run.
6. **Watch the agent.** It should: complete pre-flight guards, identify a PLANNING step, implement it, open a PR, auto-merge (for pre-prod products), exit. Total wall time: highly variable — minutes to ~half an hour depending on step complexity.

---

## Smoke test path (Puzzle Pop)

Puzzle Pop is the cleanest first test:

- Pre-prod stage → `commit` auto-merges everything → no queue route firing yet → simpler success criteria.
- Fresh PLANNING.md → no migration context for the agent to load.
- Fleet-native standards from day one → no legacy patterns the agent has to discover.

After installing all four files (central workflow, per-product stub, central-vended `SKILL.md` for both implementer and commit):

1. From `Aethrix-Labs/puzzle-pop` Actions UI → `implementer` workflow → Run with defaults.
2. Watch the workflow run. Look for:
   - Pre-flight guards pass.
   - Agent identifies a step from PLANNING.md (workflow logs will show which one).
   - A PR appears on Puzzle Pop with `tier:*` / `stage:*` / `commit-pending-merge` / `auto-merge` labels.
   - The PR body has the canonical sections (`## Summary` / `## PLANNING.md step` / `## Risk tier` / etc.).
   - CI runs (security-review) and passes.
   - Auto-merge fires. Doc updates (`PLANNING.md` checkbox, `LIFECYCLE.md`, `CHANGELOG.md`) are already in the merged commit — no separate post-merge cleanup PR appears (see `commit/SKILL.md` Step 4b).
3. Verify the new code on Puzzle Pop's staging surface.
4. Click "Run workflow" again. The agent picks the next step.

If anything goes wrong, the workflow logs show exactly where. Most likely failure modes on first run:

- **Missing vended skill files** (404 when agent tries to Read them). Fix: promote both `SKILL.md` files to central.
- **`QUEUE_SERVICE_ROLE_KEY` missing** at org level. Fix: add the org secret (you've done this once before for security-review).
- **First-PR workflow-validation gotcha** on the central workflow's first install. Fix: standard un-require → merge → re-add dance.
- **PLANNING.md format the agent can't parse.** Fix: tighten the step's acceptance criteria. The agent emits a `strategic` queue entry in this case rather than failing silently.

---

## What's deliberately NOT in this MVP

- **Wake mechanism** — hub-side workflow_dispatch fired on verify entry green-light. Tracked in `STANDARDS §11.1`. Without it, Seth manually clicks "Run workflow" after each verified step.
- **Verify entry emission (hub-side)** — `STANDARDS §9` per-step gate. Hub doesn't yet listen for merge webhooks + staging-deploy success to emit verify entries. Without it, the "did this step actually work on staging?" check is informal rather than queue-mediated.
- **`address-feedback` action implementation** — agent can't read verify entry feedback (needs hub GET endpoint, deferred).
- **Scheduled invocation** — workflow could `on: schedule: - cron: '0 */2 * * *'` for hands-off operation, but explicitly punting on this until the verify entry emission is real (otherwise the scheduler would advance past unverified steps).

All three are §11.x rows. The MVP gets you to "click button → agent does one step → exits cleanly" which is enough to validate the loop pattern and feel where the friction actually is.
