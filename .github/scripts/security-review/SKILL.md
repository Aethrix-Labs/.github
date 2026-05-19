---
name: security-review
description: Use this skill to evaluate code for semantic security issues — missing auth checks on routes, insecure direct object references (IDOR), and plaintext sensitive data-at-rest — and to triage findings from upstream deterministic security tools (gitleaks, Semgrep, dep-audit). Triggers on phrases like "security review", "security audit", "check for vulnerabilities", "is this secure", "review for security issues". Outputs structured findings JSON. Run before shipping sensitive features (auth, payments, user data, new API routes).
---

# Security Review (semantic layer)

This skill is the **semantic layer** of the layered security-review architecture (spec: `SECURITY_REVIEW_REDESIGN.md §3`). It runs three checks that need codebase reasoning and triages findings from the deterministic tools that ran upstream.

Deterministic-only patterns — committed secrets, OWASP code smells, vulnerable dependencies — are **NOT** in scope here. Those are caught by gitleaks, Semgrep, and dep-audit respectively, before this skill runs. Don't re-do their work.

---

## Inputs

Read these before starting. CI invocations make them available as files; on-demand invocations should read whatever's present in the working directory.

- **The diff under review.** Semantic checks scope to changed code, not the whole repo.
- **`deterministic-findings.json`** — output of the upstream aggregator (`/skills/security-review/aggregator.py`). Required for triage. Missing-file behavior: emit zero triage findings; log a `::warning::`.
- **Auth middleware / guard files** — filenames matching `*auth*`, `*login*`, `*session*`, `**/middleware/**`, `**/guards/**`. Read in full; they're small and stable.
- **Schema files** — only if the data-at-rest check applies: `prisma/schema.prisma`, `**/*.sql`, `**/migrations/**`.
- **`PRD.md`** — only if a finding's exploitability depends on what the product considers sensitive data.

Never read the whole codebase. Per `§5.2` of the spec, context is intentionally narrow to control cost.

---

## The three semantic checks

### Check 1 — Missing auth checks on routes

For every new or modified route, endpoint, or resolver in the diff:

- Is authentication verified before any logic runs, and where (middleware/guard vs inline)?
- Are there protected routes that lack auth entirely? Special attention to: admin routes, mutating routes (POST/PUT/PATCH/DELETE), routes returning sensitive user data.
- **Framework-aware.** Angular route guards, Express middleware chains, Hono middleware, React Router loaders, Better-Auth session checks, Supabase RLS policies. RLS counts as auth for Supabase backends — confirm policies exist on the tables the new code touches.

Severity:

- 🔴 **Critical** — a mutating or sensitive route with no auth check at all.
- 🟡 **Important** — auth check present but incomplete (authentication without authorization; admin route reachable by regular authenticated users).

### Check 2 — Insecure Direct Object References (IDOR)

For every route or operation that accesses a resource by ID:

- Is ownership verified before returning or modifying the resource? `/api/documents/123` must confirm document 123 belongs to the authenticated user.
- Bulk operations: do queries filter by the authenticated user's scope, or could they return cross-user data?
- Admin-only resources: are they reachable by regular authenticated users?
- Response shape: does the returned payload include more data than the caller should be able to see?
- Sequential integer IDs: easier to enumerate. Flag as Important; don't require UUIDs.

Severity:

- 🔴 **Critical** — any cross-user access without ownership verification; a query returns cross-user data without scoping.
- 🟡 **Important** — sequential IDs on sensitive resources; response leakage of fields the caller shouldn't see.

### Check 3 — Data-at-rest heuristics

The new v0 check. For schema files and write paths touching sensitive columns:

- **Sensitive columns** identified by name pattern: `*password*`, `*token*`, `*secret*`, `*key*`, `*ssn*`, `*pii*`, plus anything `PRD.md` explicitly flags as sensitive.
- For each sensitive column found in a schema file or migration: trace the write path in the diff. Is the value hashed (passwords), encrypted (tokens/secrets), or otherwise transformed before storage? Or written plaintext?
- Read the relevant write-path code (the INSERT/UPDATE statements in the diff that touch the column). Don't speculate about non-diff code — if the write path isn't in the diff, you can't confirm.

Severity:

- 🔴 **Critical** — plaintext password column with an active write path in the diff; plaintext API token column with an active write path.
- 🟡 **Important** — sensitive column without an obvious encryption boundary, write path incomplete or absent in the diff (can't confirm either way).
- 🔵 **Advisory** — sensitive column name pattern in a schema with no write path in the diff (informational; revisit when the write path lands).

---

## Triage of deterministic findings

Read `deterministic-findings.json`. For each finding, decide:

1. **True positive in this product's context.** Keep as-is — no triage entry needed (the original aggregator finding stands).
2. **False positive.** Emit a triage finding with `tool: "triage"` that overrides the original — `severity: "advisory"` and `description` explaining *why* it's a false positive (upstream sanitizer, test fixture, vendored library, intentional allowlist entry).
3. **True positive but severity should be re-tiered** for this product. Emit a triage finding with the new severity and the re-tier rationale in `description`.

**Never silently drop a deterministic finding.** Either accept it or override it with an explicit triage entry. Silent drops are how false-confidence creeps in.

Considerations when triaging:

- Is the flagged file in scope? Test fixtures, generated code (`dist/`, `.next/`, migrations), vendored libraries usually warrant override-to-advisory.
- Does the surrounding code or an upstream sanitizer make this finding moot?
- Is there an existing allowlist entry (`.gitleaks.toml`, `# semgrep:ignore` comment) that intentionally accepts this pattern?

---

## Output

Write `semantic-findings.json` in the same shape as the aggregator's `deterministic-findings.json` (see `/skills/security-review/aggregator.py` docstring for the canonical schema). Same shape means the future PR body composer can merge both into one report with no schema gymnastics.

```json
{
  "schema_version": "1",
  "generated_at": "<ISO 8601 UTC>",
  "summary": {
    "total":     <int>,
    "critical":  <int>,
    "important": <int>,
    "advisory":  <int>,
    "by_tool": {
      "semantic-auth":      <int>,
      "semantic-idor":      <int>,
      "semantic-data-rest": <int>,
      "triage":             <int>
    }
  },
  "findings": [
    {
      "tool":        "semantic-auth" | "semantic-idor" | "semantic-data-rest" | "triage",
      "rule_id":     "<short stable identifier — used for suppression and audit>",
      "severity":    "critical" | "important" | "advisory",
      "title":       "<short label>",
      "description": "<what the issue is + suggested fix + (for triage) why the override>",
      "file":        "<path>" | null,
      "line_start":  <int> | null,
      "line_end":    <int> | null,
      "commit_sha":  null,
      "package":     null,
      "raw":         { "<reasoning trail, file references, override target, etc.>": "..." }
    }
  ]
}
```

`tool` values for semantic findings use the `semantic-*` prefix to distinguish them from deterministic-tool findings (`gitleaks`, `semgrep`, `dep-audit`). Triage entries use `tool: "triage"` and put the overridden finding's identity in `raw` (e.g., `raw.overrides = { tool, rule_id, file, line_start }`).

---

## Execution surfaces

This skill runs in two contexts, both governed by `SECURITY_REVIEW_REDESIGN.md`:

- **CI (autonomous)** — invoked by `.github/workflows/security-review.yml` via `claude-code-action` using `CLAUDE_CODE_OAUTH_TOKEN` (auth per `STANDARDS.md §14`). Conditional execution per spec `§3.3` — the workflow decides whether to invoke based on diff scope and deterministic findings. Output is `semantic-findings.json`. The downstream PR body composer (pending, `§11.2`) renders the unified report. No interactive prompts during execution.
- **On-demand (interactive)** — invoked as a Claude Code subagent (spec `§10`). Runs unconditionally; no `§3.3` gating. Output is still `semantic-findings.json`; the invoking host session renders the JSON for the user and may offer auto-fix on Critical/Important issues. Auto-fix is the host's responsibility, not this skill's.

Neither surface waits for user input mid-run. The interactive flow happens *after* the skill completes, in the host session.

---

## Edge cases

- **No diff available.** On-demand: ask which files to review. CI: this shouldn't happen — the workflow always has a PR diff. If it does, emit zero findings and log a `::warning::`.
- **Very large diff.** Prioritize files touching auth, user data, payments, schema. Note in the relevant findings' `raw` field that other files weren't fully reviewed.
- **Test files in diff.** Skip semantic checks on test files. Deterministic tools handle their own test-file exclusions via standard config.
- **Generated files in diff** (`dist/`, `.next/`, migrations, etc.). Skip semantic checks.
- **Unknown framework.** Run checks at a conceptual level; note in `raw` that framework-specific patterns couldn't be verified.
- **`deterministic-findings.json` missing.** Triage section emits zero findings; log a `::warning::`. The three semantic checks still run on the diff.
- **`PRD.md` missing or unreadable.** Data-at-rest heuristic uses the default sensitive-column patterns only. Note in `raw` for affected findings.

---

## Guidelines

- **Be specific about exploitability.** Describe what an attacker could actually do, not just that the pattern is bad. "An attacker could read any user's documents by changing the ID in the URL" beats "IDOR vulnerability present."
- **Every finding includes a suggested fix.** Concrete code-shape recommendations where useful. Don't flag without proposing a remedy.
- **Don't flag theoretical issues.** Only flag patterns present in the actual diff, not hypothetical concerns about code you haven't read.
- **Framework-aware reasoning.** Recognize the right patterns for the stack in use. The fleet is mostly TypeScript on Cloudflare (React Router v7, Hono, Better-Auth) per `FLEET_TECH.md`; products may diverge.
- **Triage is mandatory, not optional.** Every deterministic finding gets either an implicit accept (no triage entry) or an explicit override (triage entry with rationale). Silent drops compromise the audit trail.
- **Secrets in git history are out of scope here.** Gitleaks owns that ground. If you find a secret pattern in the diff that Gitleaks should have caught and didn't, emit a `triage` finding noting the upstream gap rather than re-detecting it yourself.
