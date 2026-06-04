---
name: milestone-close
description: "CI-only companion to the commit skill, invoked when the step being committed closes its PLANNING.md milestone (all sibling top-level checkboxes under the `## M<n>` heading are now `[x]`). Two duties: (1) archive the closed milestone to PLANNING_ARCHIVE.md in the same PR, via compact.py --milestone; (2) after the PR exists, emit a medium-tier `follow-up` queue entry summarizing what the milestone shipped and how Seth should manually test it. Not invoked directly by Seth — for interactive bulk compaction use planning-compactor instead."
canonical_source: fleet-command-center/skills/milestone-close/SKILL.md
---

# Milestone-Close Skill

Runs inside the commit flow when the committed step is **milestone-closing** per `STANDARDS.md §9` ("Milestone-close behavior"). Deliberately lean — this loads into the CI implementer's context window; the interactive sibling `planning-compactor` carries the full compaction spec.

Two phases, called at different points in the commit skill's flow:

- **Phase 1 — archive** (commit Step 4b, before `git add`): cut the closed milestone from `PLANNING.md` into `PLANNING_ARCHIVE.md` so both land in the same PR as the closing step.
- **Phase 2 — follow-up emission** (commit Step 5.5, after a PR exists): POST a medium `follow-up` queue entry — shipped summary + manual test plan for Seth.

## Phase 1 — Archive the closed milestone

**1a. Confirm closure.** After commit Step 4b flips the step's checkbox, verify every top-level `- [ ]`/`* [ ]` under the milestone's `## M<n>` heading is now `[x]` (recursively, including `### Steps` subsections). Not all closed → this skill doesn't apply; return to the commit flow.

**1b. Compose the shipped summary.** One paragraph (1–3 sentences) describing what the milestone shipped at feature/outcome level — lean on the CHANGELOG entries for the milestone's steps (including the one being written in this same commit). Drop implementation detail. This paragraph is used twice: the archive block now, the queue packet's `goal` in Phase 2 — compose once, reuse.

**1c. Run the archiver** (script vended from the planning-compactor bundle):

```bash
python3 .fleet-ci/.github/scripts/planning-compactor/compact.py \
  --apply --milestone <M-id> --summary "<composed paragraph>" docs/PLANNING.md
```

- Exit 0 → `PLANNING.md` (milestone cut, overview table re-rendered) and `PLANNING_ARCHIVE.md` (summary block prepended) are both written. They ride in the same commit.
- Exit 1 "not archivable" → a checkbox is still open; recheck 1a. Do NOT force it.
- Exit 1 "not found" → if `PLANNING_ARCHIVE.md` already contains a `### <M-id>` heading, a prior run archived it — idempotent success, skip to Phase 2. Otherwise the milestone heading doesn't match the §9 contract; skip archiving, note "milestone archive skipped: <reason>" in the PR body `## Notes`, and still do Phase 2.
- Script missing from `.fleet-ci/` → same: skip archive, note it, still do Phase 2. Archiving is a nicety; the milestone summary for Seth is the point.

Archive failure handling otherwise follows commit's "inline doc-update fails" row (stop before PR, exception entry).

## Phase 2 — Emit the milestone follow-up entry

Fires once per closed milestone, after commit Step 4d opened the PR (any of Route A or B; Route C never reaches here). Non-blocking: a failed POST must never block the merge.

**2a. Compose the manual test plan.** A numbered list of actions **Seth performs by hand** to confirm the milestone works — written for a human at a browser/device, not a test runner:

- Source the staging (or device) URL from `DEPLOYMENT.md` / `RUNBOOK.md`.
- One numbered item per user-visible behavior the milestone shipped (pull from the milestone's step texts + CHANGELOG `**User-facing:**` sections). Each item: where to go, what to do, what to expect.
- Pure-infra milestones: say so, and give 1–2 smoke checks (page loads, logs clean) instead.

**2b. POST the packet** (envelope per `STANDARDS.md §8`):

```bash
curl -X POST "${HUB_BASE_URL:-https://sethgibson.com}/api/v1/queue/entries" \
  -H "Content-Type: application/json" \
  -H "x-service-role-key: ${QUEUE_SERVICE_ROLE_KEY}" \
  -d @- <<'JSON'
{
  "request_id": "<SHA256 of repo + M-id + 'milestone-close'>",
  "entry_type": "follow-up",
  "risk_tier": "medium",
  "agent_name": "milestone-close",
  "title": "[<PRODUCT_SLUG>] <M-id> complete: <milestone name> — manual test plan",
  "goal": "<the Phase 1b shipped-summary paragraph>",
  "attempts": [
    "<one line per step the milestone comprised: M<n>.<x> — <short step text> (PR #<m> where known)>"
  ],
  "ask": "Manually verify on <staging-url>:\n1. <action — expected result>\n2. ...",
  "recommendation": "Dismiss once manual testing passes. File anything broken as a BACKLOG.md item (or feedback on the step's verify entry).",
  "artifacts": [
    { "label": "Closing PR", "href": "<PR_URL>", "artifact_type": "github-pr" },
    { "label": "Archive entry", "href": "<github URL to docs/PLANNING_ARCHIVE.md>", "artifact_type": "doc" },
    { "label": "Staging", "href": "<staging-url>", "artifact_type": "staging-url" }
  ]
}
JSON
```

`request_id` is rooted in repo + milestone ID — re-runs on the same milestone dedupe server-side (200, not a duplicate entry). Omit the Staging artifact if no deployed surface exists yet.

**On non-2xx or missing `QUEUE_SERVICE_ROLE_KEY`:** log `::warning::milestone-close: follow-up queue write skipped (<reason>)` and continue — the commit flow proceeds normally. Include the test plan in the commit skill's final chat/log output so it isn't lost.

## What this skill does NOT do

- **Bulk compaction or interactive dry-run/approval.** That's `planning-compactor` (Cowork, human-invoked). This skill archives exactly the one milestone that just closed, unconditionally, no approval loop — risk is covered by the PR gate it rides inside (doc-only LOW diff per §9).
- **Gate or block anything.** The follow-up entry never auto-expires (§7) but is non-blocking by definition; the loop's blocking gates remain the verify entry and the hub's prod-deploy gate entry (§9), which are separate and hub-emitted.
- **Touch `LIFECYCLE.md`.** Commit Step 4b already set `next_milestone:` from what remains in `PLANNING.md`.
- **Run outside the commit flow.** No standalone trigger; the commit skill is the only caller.
