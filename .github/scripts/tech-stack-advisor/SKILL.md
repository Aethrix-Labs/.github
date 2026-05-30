---
name: tech-stack-advisor
description: "Use this skill when the user wants to decide a tech stack for a new product. Triggers on phrases like \"what stack should I use\", \"help me pick a tech stack\", \"tech stack recommendation\", \"what should I build this with\", or any request to commit a product to specific frameworks, databases, or hosting before implementation begins. Always run after the PRD is finalized (with a publish-form declared) and before `PLANNING.md` is written. Reads `FLEET_TECH.md` to bias toward fleet consolidation and away from speculative new ops surfaces. Produces a HIGH-tier escalation packet and a draft `DECISIONS.md` entry."
---

# Tech Stack Advisor

Decides the tech stack for a single product. Reads the PRD, evaluates candidate stacks against `FLEET_TECH.md`, drafts a `DECISIONS.md` entry, and produces a HIGH-tier escalation packet for Seth's approval before anything lands.

**Risk tier: HIGH (always).** Per `STANDARDS.md §9` elevation rule, tech-stack proposals are one-time per-product decisions with cascading downstream consequences — frontend / backend / DB / hosting commitments, schema-shape choices, third-party platform contracts. Every proposal queues, including ones that reuse fleet defaults verbatim, so Seth can consciously reaffirm or override. Never auto-pass.

**Execution surface: Claude Code subagent** (per `SKILL_AUDIT.md §3.3`). Runs inside a per-product Claude Code session against the product repo. Decision capture is currently a transitional present-and-confirm in chat; future: emit the packet to the decision queue (`STANDARDS.md §7`) when the queue exists. Packet shape doesn't change.

---

## Required inputs

The skill blocks before running if any of these are missing:

1. **`docs/PRD.md` with a Publish form section.** The PRD must declare the deployment target — one of: web app, mobile app, SaaS, agent, automation — plus tenancy and distribution qualifiers when relevant (e.g., "web app, single-user, internal-only"). Vocabulary per `STANDARDS.md §11.3` (G3 resolution). Without this, candidates can't be scoped.
2. **`FLEET_TECH.md`**, read from the fleet command center repo at `~/products/docs/FLEET_TECH.md` (Windows: `C:\Users\wgibs\products\docs\FLEET_TECH.md`). Path convention per `STANDARDS.md §2.1`. Read it fully on every run — hard defaults, soft conventions, opt-ins, parked surfaces, and per-product inventory all live here.
3. **`docs/LIFECYCLE.md`** for the target product (if the repo exists yet). Used for the `monetized` flag and stage context. New products being scaffolded won't have this yet — that's fine; default `monetized: false`, stage `idea`.

If `PRD.md` is missing or has no Publish form section, stop and ask Seth to address it. Don't guess the form factor — the rest of the workflow depends on it. If `FLEET_TECH.md` can't be located, escalate as a fleet-infra access issue; don't proceed without it.

---

## Workflow

### Step 1: Read the inputs

- Read `PRD.md` end-to-end. Extract: publish form, core features, scale expectations, real-time / collaborative requirements, auth needs, data complexity, deployment constraints, timeline.
- Read `FLEET_TECH.md` end-to-end. Note hard defaults (§1), soft conventions (§2), documented opt-ins (§3), parked surfaces (§4), per-product inventory (§7), and re-evaluation triggers (§8).
- If `LIFECYCLE.md` exists, read its header.

### Step 2: Map the PRD onto fleet layers

For each layer relevant to this publish form (hosting, backend compute, database, auth, mobile, etc.), assign one of three dispositions:

| Disposition | When | Bias |
|---|---|---|
| **Reuse fleet default** | Hard default (`FLEET_TECH.md §1`, Cloudflare ops surface) or soft convention (§2) fits the PRD without forcing it | Strongly prefer |
| **Take a documented opt-in** | PRD pushes into a case `FLEET_TECH.md §3` already lists (Postgres via Hyperdrive, Swift native iOS, specialized inference host, Coolify for non-customer-facing) | Use without ceremony |
| **Deviate** | PRD legitimately needs something the fleet doesn't yet support and no opt-in covers it | Allowed when fit warrants; flag the consolidation cost loudly |

Rules of the road:

- **Hard defaults (Cloudflare operational surface)** require strong justification to deviate. "I want Vercel" is not sufficient; "this workload needs sustained GPU inference Cloudflare can't host" is.
- **Soft conventions (RR7, Hono, Better-Auth, RN+Expo, Drizzle, pnpm, TypeScript)** can be deviated from without ceremony — just record the deviation in `DECISIONS.md` per `FLEET_TECH.md §2` so the convention can be re-evaluated against real evidence.
- **Framework / language / library choices outside the soft conventions** are evaluated fresh per product. There is no fleet thumb on the scale (G8 retired 2026-05-14 — see `STANDARDS.md §11.3`).
- **Fit wins over consolidation when they conflict.** A technology that genuinely fits the product better should be recommended — but the packet must call out the consolidation cost honestly: new subscription, new ops surface, new mental model the fleet has to carry, new re-evaluation trigger created.

### Step 3: Build 2–3 candidate stacks

Each candidate is a complete, coherent stack — not a menu of swappable parts. Pick candidates that span the real decision space for this product:

- **Candidate A — fleet-aligned.** Reuses hard defaults and soft conventions where they fit; takes documented opt-ins where the PRD requires.
- **Candidate B — alternative.** Only if a real reason exists: the PRD has a requirement the fleet can't meet cleanly, OR a specific technology has a clear product-fit advantage worth the consolidation cost.
- **Candidate C — second alternative or alternative configuration.** Only if it adds signal. Two candidates is fine if the third would be padding.

Never pad the table. A forced third option that's clearly inferior wastes Seth's review time.

### Step 4: Score the candidates

Use ✅ Strong / ⚠️ Adequate / ❌ Weak across these dimensions:

| Dimension | What to assess |
|---|---|
| **Fit for requirements** | How well does this stack address the core PRD needs? |
| **Fleet consolidation** | Does this reuse what the fleet already runs, or does it introduce new ops surface / subscriptions? |
| **Dev speed** | How fast can MVP be built on this stack? |
| **Scalability** | Can it grow if the product takes off? |
| **Complexity** | How much infra / config overhead? |
| **Cost (MVP)** | Honest $ / $$ / $$$ at the MVP stage; note free-tier coverage |
| **Cost (scale)** | At what point does cost get uncomfortable? |
| **Claude Code support** | How well does the agent path handle this stack? |

### Step 5: Make a recommendation

State a clear recommendation. Don't hedge — the matrix exists to inform Seth, not to obscure your call. 3–5 sentences that reference the PRD requirements directly and call out the one or two things that tipped the decision.

### Step 6: Draft the `DECISIONS.md` entry

Draft to a buffer; don't write to disk until Step 8 approval lands.

```markdown
## <YYYY-MM-DD> — Tech Stack

**Decision:** <Stack name>

**Publish form:** <copied from PRD verbatim>

**Alternatives considered:** <Candidate B>, <Candidate C if applicable>

**Stack:**
- Frontend: <e.g., React Router v7 + TailwindCSS>
- Backend: <e.g., Hono on Cloudflare Workers>
- Database: <e.g., Cloudflare D1>
- Auth: <e.g., Better-Auth>
- Hosting: <e.g., Cloudflare Pages>
- Mobile: <if applicable>

**Consolidation status:**
- Reuses fleet hard defaults: <list, or "none">
- Reuses fleet soft conventions: <list, or "none">
- Documented opt-ins taken: <list with `FLEET_TECH.md §3` reference, or "none">
- Deviations introduced: <list with rationale, or "none">

**What this commits the fleet to:**
- New subscriptions / surfaces / ops mental model: <list, or "none">
- New re-evaluation triggers: <e.g., "first product on Postgres-via-Hyperdrive — confirms opt-in path is clean per FLEET_TECH.md §8", or "none">

**Rationale:**
<3–5 sentences. Should make sense to someone reading it cold in 6 months.>

**Tradeoffs accepted:**
<1–2 sentences on what was knowingly traded away.>
```

### Step 7: Assemble the escalation packet

Packet shape per `STANDARDS.md §8` (approval body) with tech-stack-specific fields per `SKILL_AUDIT.md §3.3`:

1. **Goal** — "Decide the tech stack for `<product>`."
2. **Publish form** — copied from the PRD verbatim.
3. **Consolidation status** — the structured block from Step 6 (reuses / opt-ins / deviations).
4. **Comparison table** — the scored matrix from Step 4 plus a 2–3 sentence prose note per candidate explaining what it is, why it's a contender, and its main tradeoff.
5. **Recommendation** — chosen stack plus the Step 5 rationale.
6. **What this commits the fleet to** — the structured block from Step 6.
7. **The ask** — "Approve this stack so the DECISIONS.md entry can land and scaffold-new-product / planning can proceed."

**Always emit the packet, even when the proposal reuses fleet defaults verbatim.** The point is conscious reaffirmation, not skipping the gate. In the default-match case the packet is short — Consolidation status reads "reuses everything"; Deviations introduced is "none"; What this commits the fleet to is "no new surfaces" — but the packet still emits and Seth still confirms.

### Step 8: Present for approval

Show the packet in chat. Ask Seth to:

- **Approve** → proceed to Step 9.
- **Modify** → adjust the recommendation (e.g., swap candidate B in, change a layer) and re-present.
- **Reject** → capture the rejection rationale in chat, don't write to `DECISIONS.md`, stop.

> **Transitional behavior.** While the decision queue isn't live, this is an in-chat interaction. When the queue ships (`STANDARDS.md §7`), this step emits the packet as an `approval request` entry; the resolution flows back through the queue's standard actions (approve / modify-in-Cowork / reject). The packet shape and Step 6 draft are unchanged.

### Step 9: On approval, write the entry

- Append the Step 6 draft to `docs/DECISIONS.md`. Newest entries go at the top of the entry list, preserving any header.
- If `DECISIONS.md` doesn't exist, create it with:

  ```markdown
  # Architecture Decision Records

  A log of key technical decisions made during development, with rationale.

  ---
  ```

- **Append a row to `FLEET_TECH.md §7` (per-product inventory)** for every approved stack — that table is the running fleet census. Surface the proposed row as a diff for Seth to approve; don't write to `FLEET_TECH.md` autonomously since it's a fleet-level doc.
- **If the decision also introduces a new fleet surface, a new opt-in, or a soft-convention deviation worth tracking,** flag that `FLEET_TECH.md §2` or §3 needs an additional append in the same session. Same rule: surface the diff, don't edit autonomously.

---

## Edge cases

- **No PRD found** → stop, ask Seth to run `brainstorm-to-prd` first.
- **PRD has no Publish form section** → stop, ask Seth to add one. Don't infer. Vocabulary per `STANDARDS.md §11.3` G3.
- **`FLEET_TECH.md` not reachable** (per-product Claude Code session can't read the fleet command center repo) → escalate as a fleet-infra access issue; don't proceed.
- **Product strongly fits a fleet default** → still build candidates, still emit the packet. The packet is short but the gate fires.
- **Product needs something not in `FLEET_TECH.md §3` opt-ins** → recommend the deviation if fit warrants. After the decision lands, the resolution may surface a new opt-in row for `FLEET_TECH.md §3` — flag this for Seth to add.
- **Seth wants to anchor a specific technology upfront** → take it as a constraint, evaluate the rest of the stack around it, and call out the consolidation impact honestly in the packet.
- **`DECISIONS.md` already has entries** → append. Never restructure existing records.
- **Mobile + web product** → evaluate web and mobile independently; reuse the same backend across both when possible (matches `FLEET_TECH.md §2` soft convention to keep one language across surfaces).

---

## Guidelines

- **Be decisive.** A clear recommendation with honest tradeoffs is more useful than a balanced non-answer. The packet is what Seth uses to override.
- **Reference the PRD by section.** Generic stack advice is easy to find. The value is advice specific to this product's PRD.
- **No fleet-wide framework defaults.** G8 retired 2026-05-14. Don't write "always X for Y" — evaluate fresh against `FLEET_TECH.md §2` soft conventions.
- **Don't pad the table.** If only two options make sense, show two.
- **Cost estimates are honest.** Use $ / $$ / $$$ and note free-tier coverage.
- **`DECISIONS.md` is a permanent record.** Write it as if someone reads it cold in 6 months. Avoid "as discussed" or context-dependent language.

---

## References

- `STANDARDS.md §9` — risk tier classification; HIGH-tier elevation rule for tech-stack proposals
- `STANDARDS.md §8` — escalation packet contract (approval body)
- `STANDARDS.md §11.3` — G8 retirement (no fleet-wide framework default); G3 resolution (publish-form vocabulary)
- `STANDARDS.md §13` — canonical skill location (`/skills/tech-stack-advisor/SKILL.md`)
- `FLEET_TECH.md` — fleet tech inventory and consolidation defaults; bias source for this skill
- `SKILL_AUDIT.md §3.3` — this skill's audit (the redesign brief)
