# input-agent

**Status:** Active — synthesize mode only (M8). Route + dedup modes are scaffolded but inert.

**When to use this skill:** Use synthesize mode automatically — it is not user-invoked. It fires after every contribution write (via MCP `start_thread` / `log_to_thread` and hub UI contribution form). Use dedup mode (when active) after a new contribution arrives to check for overlapping threads.

---

## Modes

### synthesize (active — M8)

Reads all contributions on a thread and produces or updates the `synthesis` field. Runs automatically on every contribution write. Also invokable on demand from the thread detail page via "Re-synthesize".

**Input (via `runSynthesizer` in `app/lib/synthesizer.server.ts`):**
- Thread title
- All contributions in time order (channel, submitter, summary)

**Output:**
- 2–4 sentence synthesis written to `ideas.synthesis`
- Captures: current state, decided vs open, conflicts (named explicitly when present)
- Conflict-flagging escalation: if the synthesizer detects a contradiction it cannot resolve from context, it should emit an `approval` queue entry with a conflict-flavored body. (Not yet wired in M8 — the synthesizer updates `synthesis` text only; queue emission is a follow-up.)

**Model:** `claude-haiku-4-5-20251001` (G18 assignment). Upgrade to Sonnet case-by-case if quality is insufficient.

**Failure behavior:** If `ANTHROPIC_API_KEY` is absent or the Anthropic API returns an error, the synthesizer fails silently — the contribution write still succeeds, and `synthesis` stays at its prior value.

### route (inert — M8)

Scope per `~/products/docs/IDEA_THREADS.md §8` (deferred — routing-suggest): route ambiguous contributions to the right surface/thread. Not active in M8 — routing for Seth's own MCP contributions is handled by the conversation (Seth names the thread explicitly). Future scope: non-Claude inbound (email/SMS if those channels activate).

### dedup (inert — M8)

Scope per `~/products/docs/IDEA_THREADS.md §8` (deferred — dedup): scan existing threads for near-duplicates when a new contribution arrives; emit an `approval` entry proposing a merge. Never auto-merges. Activates when first needed (trigger: first time Seth notices two threads that should obviously be merged).

---

## Activity trail

This skill does not write agent_activity records in M8. Synthesizer runs are fast, synchronous, and logged implicitly in the `ideas.synthesis` update — no separate trail record.

---

## Cross-references

- Implementation: `app/lib/synthesizer.server.ts`
- MCP write path: `app/routes/api.mcp.ts` (calls runSynthesizer after every tool write)
- Hub UI write path: `app/routes/ideas.tsx` action (calls runSynthesizer after add_contribution and capture)
- Thread schema: `app/db/schema.ts` — `ideas.synthesis` column
- Design spec: `~/products/docs/IDEA_THREADS.md §4` (synthesizer)
- Model assignment: `STANDARDS.md G18`
