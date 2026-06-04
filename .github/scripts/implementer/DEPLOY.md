# Implementer Skill — Deployment Guide

This doc accompanies `/skills/implementer/SKILL.md`. It captures the **deployment shape** for the implementer's CI surface: the central reusable workflow at `Aethrix-Labs/.github` and the thin per-product stub that opts each product into the autonomous loop.

Per `STANDARDS.md §17.4`, workflow YAML doesn't live in `/skills/`. The YAMLs below are documentation for the deployment, not the deployed files themselves — Seth applies them to the respective deployment locations manually (matches the security-review pattern).

**Hub-only dispatch (2026-06-03).** The implementer runs solely from the hub's autonomous-dev loop (`dispatchImplementer`). Manual Actions-UI triggers are unsupported — the skill's Guard 2 rejects any run without a directed target. The stub still declares `workflow_dispatch` because that's the event the hub fires.

---

## Architecture

```
              ┌──────────────────────────────────────────────────┐
              │  Hub orchestrator (play-button loop)             │
              │  dispatchImplementer → workflow_dispatch         │
              │  inputs: { session_id, hub_url,                  │
              │            target_kind, target_ref }             │
              └────────────────────┬─────────────────────────────┘
                                   ▼
       ┌───────────────────────────────────────────────────────┐
       │  <product-repo>:.github/workflows/implementer.yml     │
       │  (per-product stub)                                   │
       │  Calls central reusable workflow, `secrets: inherit`  │
       └───────────────────────────┬───────────────────────────┘
                                   │ jobs.<job>.uses
                                   ▼
       ┌───────────────────────────────────────────────────────┐
       │  Aethrix-Labs/.github:.github/workflows/              │
       │     implementer-callable.yml                          │
       │  - Checks out consumer repo + .fleet-ci/              │
       │  - Invokes claude-code-action with the SKILL.md       │
       │    prompt                                             │
       └───────────────────────────┬───────────────────────────┘
                                   │ Read .fleet-ci/.github/scripts/implementer/{SKILL,PACKETS}.md
                                   │ Read .fleet-ci/.github/scripts/commit/SKILL.md
                                   ▼
                         ┌─────────────────────┐
                         │  Claude agent does  │
                         │  one step of the    │
                         │  directed target    │
                         │  + invokes commit   │
                         │  + tick-reports     │
                         └─────────────────────┘
```

The loop: tick-report → hub orchestrator decides (tick-again / paused-merge / paused-queue / done) → re-dispatch with the same sticky target. See `sethgibson-com:docs/AUTONOMOUS_DEV.md`.

---

## Central reusable workflow

**Target:** `Aethrix-Labs/.github:.github/workflows/implementer-callable.yml`

```yaml
name: implementer (callable)

on:
  workflow_call:
    inputs:
      product_slug:
        description: "Product slug (defaults to repo basename)"
        type: string
        required: false
        default: ""
      session_id:
        description: "Autonomous session ID from the hub play-button loop."
        type: string
        required: false
        default: ""
      hub_url:
        description: "Hub base URL for posting tick-reports. Defaults to https://sethgibson.com."
        type: string
        required: false
        default: "https://sethgibson.com"
      target_kind:
        description: "Directed target kind from the hub picker: 'backlog' | 'milestone'. Empty trips the skill's Guard 2 — manual dispatch is unsupported."
        type: string
        required: false
        default: ""
      target_ref:
        description: "Directed target ref: BL-<n> for a backlog item, M<n> for a milestone."
        type: string
        required: false
        default: ""

permissions:
  contents: write # commit + push from the implementer agent
  pull-requests: write # gh pr create / merge
  id-token: write # required by anthropics/claude-code-action@v1

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
      - uses: actions/checkout@v4 # consumer repo at $GITHUB_WORKSPACE
        with:
          fetch-depth: 0 # needed for full git history (commit skill reads log)

      - uses: actions/checkout@v4 # central repo (SKILL.md + scripts)
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
          # Used by the implementer's queue packets + activity records and the
          # commit skill's queue emission path (per STANDARDS §14
          # multi-storage credentials). Same secret security-review uses.
          QUEUE_SERVICE_ROLE_KEY: ${{ secrets.QUEUE_SERVICE_ROLE_KEY }}
          # Tick-report auth token (separate from QUEUE_SERVICE_ROLE_KEY per m2m convention).
          AUTONOMOUS_TICK_TOKEN: ${{ secrets.AUTONOMOUS_TICK_TOKEN }}
          # Dispatch payload passed through to the agent via env.
          IMPLEMENTER_PRODUCT_SLUG: ${{ inputs.product_slug }}
          IMPLEMENTER_SESSION_ID: ${{ inputs.session_id }}
          IMPLEMENTER_HUB_URL: ${{ inputs.hub_url }}
          # Directed target — required by the skill's Guard 2.
          IMPLEMENTER_TARGET_KIND: ${{ inputs.target_kind }}
          IMPLEMENTER_TARGET_REF: ${{ inputs.target_ref }}
        with:
          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
          prompt: |
            You are running as the **implementer agent** in CI.

            Read `.fleet-ci/.github/scripts/implementer/SKILL.md` and follow
            it exactly. It defines the pre-flight guards, the flow (resolve
            the directed target → implement the next unblocked step → invoke
            commit → exit), and the failure modes.

            When ready to commit, read `.fleet-ci/.github/scripts/commit/SKILL.md`
            and follow its flow. The commit skill handles tier classification,
            PR body composition, queue emission, and merge routing.

            Runtime context:
            - Working directory has the full consumer repo (fetch-depth: 0).
            - Directed target: $IMPLEMENTER_TARGET_KIND / $IMPLEMENTER_TARGET_REF.
            - Product slug: $IMPLEMENTER_PRODUCT_SLUG (or derive from $GITHUB_REPOSITORY).

            Exit after ONE step. The verify gate is the loop boundary.
            Do not chain into the next step.
          claude_args: |
            --model claude-sonnet-4-6
            --max-turns 80
            --allowedTools Read,Bash,Write,Edit,Glob,Grep
```

**Notes on this YAML:**

- `--max-turns 80` is intentionally generous. The implementer does open-ended engineering work; the commit skill alone runs several steps with bash invocations. Tune down if cost becomes an issue.
- Model is `claude-sonnet-4-6` per `STANDARDS.md §14`. The implementer is reasoning-heavy, so Haiku would underperform.
- Tools allow Read/Bash/Write/Edit/Glob/Grep. No `WebFetch` — the agent shouldn't need external web access for fleet-native products.
- `permissions: contents: write` is required for the agent to push commits.

---

## Per-product stub workflow

**Target:** `<product-repo>:.github/workflows/implementer.yml` (one file per opted-in product)

```yaml
name: implementer

on:
  workflow_dispatch:
    inputs:
      action:
        # Vestigial — the hub's dispatchImplementer still sends this field, and
        # GitHub rejects dispatches carrying undefined inputs. Not forwarded to
        # the callable. Remove once the hub stops sending it.
        description: "Vestigial (hub compatibility). Always next-step."
        type: string
        required: false
        default: "next-step"
      session_id:
        description: "Autonomous session ID from the hub play-button loop."
        type: string
        required: false
        default: ""
      hub_url:
        description: "Hub base URL for tick-report POST. Defaults to production hub."
        type: string
        required: false
        default: "https://sethgibson.com"
      target_kind:
        description: "Directed target kind from the hub picker: 'backlog' | 'milestone'. Empty trips the skill's Guard 2 — manual dispatch is unsupported; start runs from the hub."
        type: string
        required: false
        default: ""
      target_ref:
        description: "Directed target ref: BL-<n> for a backlog item, M<n> for a milestone."
        type: string
        required: false
        default: ""

permissions:
  contents: write
  pull-requests: write
  id-token: write

jobs:
  implementer:
    uses: Aethrix-Labs/.github/.github/workflows/implementer-callable.yml@main
    secrets: inherit
    with:
      session_id: ${{ inputs.session_id }}
      hub_url: ${{ inputs.hub_url }}
      target_kind: ${{ inputs.target_kind }}
      target_ref: ${{ inputs.target_ref }}
```

**Notes:**

- `secrets: inherit` passes through `CLAUDE_CODE_OAUTH_TOKEN`, `QUEUE_SERVICE_ROLE_KEY`, and `AUTONOMOUS_TICK_TOKEN` from org-level secrets to the central reusable workflow.
- **`permissions:` at the caller is required, not optional.** GitHub Actions only passes down permissions the caller has explicitly granted; the callee's own `permissions:` block is a _ceiling_, not a grant. Missing this block manifests as `"is requesting ... write, but is only allowed ... read|none"` at workflow validation time (puzzle-pop smoke test, 2026-05-20). The block must match (or exceed) the central workflow's `permissions:` declaration verbatim.
- **Repo-level Actions settings caveat.** If the repo's Settings → Actions → "Workflow permissions" is read-only with no bump path, the block above won't help; bump the repo (or org) setting first.
- `@main` ref means the product picks up central updates automatically on next dispatch. Pin to a tag if a product needs to freeze.
- The `action` input is dispatch-compatibility only: the hub's `dispatchImplementer` still includes `action: "next-step"` in its payload, and GitHub 422s a dispatch with undefined inputs. It is NOT forwarded to the callable. Once the hub drops the field, delete the input here.

---

## Update / rollout order

When changing the workflow inputs (like the 2026-06-03 cleanup that removed `verify_entry_id` and stopped forwarding `action`):

1. **Per-product stubs first** — they must stop forwarding an input before the callable stops declaring it, or the `workflow_call` fails with "invalid input." Stubs live in: sethgibson-com, puzzle-pop, moneycomb, run-intelligence.
2. **Central callable second** (`Aethrix-Labs/.github`) — applied manually by Seth.
3. **Hub last** — `dispatchImplementer` can drop the vestigial `action` field any time after the stubs stop requiring it; then remove the `action` input from the stubs.
4. **Re-vend the skill bundle** (`SKILL.md`, `PACKETS.md`, `DEPLOY.md`) per `STANDARDS §17.5` whenever skill behavior changed alongside.

New-product installation: add the stub via regular PR, confirm org secrets exist (`CLAUDE_CODE_OAUTH_TOKEN`, `QUEUE_SERVICE_ROLE_KEY`, `AUTONOMOUS_TICK_TOKEN`), register the product in the hub, then start a session from the hub's play button. Validation: the run resolves the directed target, opens a PR with the canonical body sections and `tier:*` / `stage:*` / `commit-pending-merge` labels, tick-reports back, and the orchestrator advances or pauses per the gating rules.
