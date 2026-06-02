---
name: design-url-to-planning
description: Use this skill when a Claude Design session is finished and its result needs to become implementation work in an existing fleet product — i.e. turn a design into PLANNING.md milestones. Triggers when the user pastes a Claude Design URL (https://api.anthropic.com/v1/design/h/...) into a product project, or says things like \"plan the build for this design\", \"turn this design into milestones\", \"write the PLANNING steps for this\", \"I finished the design — add it to the plan\", \"break this design into implementation steps\", or \"the new design is done, what's the build plan\". This skill does NOT generate designs (that's a Claude Design session, reached via `design-update` for visual-direction changes) and does NOT do feature brainstorming or definition (that's a Seth's-lane conversation). It assumes the design already exists and writes the milestone(s) to implement it. Use it whenever a Claude Design URL needs to become buildable work.
---

# Claude Design → Planning

Turn a finished Claude Design session into concrete, buildable work: snapshot the design into the product repo, understand it against the product's existing code and conventions, and write conformant milestone(s) into `docs/PLANNING.md` that the autonomous implementer loop can execute.

This skill operates against a product's repo (rooted at `~/products/<product>/`). The design and the creative decisions behind it already happened in Claude Design (Seth's lane) — this skill is the delegated bridge from that design to an implementation plan. It does not brainstorm, define features, or generate UI; it translates an existing design into steps.

**Where this runs — prefer Claude Code.** The output is a markdown plan, but its quality depends on Step 2's codebase mapping: reading across the product's source to work out what data, endpoints, components, and wiring the design implies, and what already exists to reuse. That multi-file code comprehension is Claude Code's strength, so run this skill from a Claude Code session in the product repo (or as a Claude Code subagent). A vague grasp of the code produces vague steps, and vague steps are what the autonomous implementer chokes on. The one piece that can originate elsewhere is the Claude Design snapshot itself — if `design-update` already imported it in a Cowork session, Step 1 reuses that existing `SOURCES.md` snapshot rather than re-importing, so the Claude Code run picks up wherever the design landed.

**Where it sits.** A visual change typically flows: `design-update` (runs the Claude Design session, produces a URL) → **this skill** (URL → `PLANNING.md` milestones) → optionally `mockup-to-style-guide` (same snapshot → updated `STYLE_GUIDE.md` / `tokens.json`, if the design system itself changed) → implementer loop. This skill and `mockup-to-style-guide` are siblings: both consume the same Claude Design snapshot, producing different artifacts.

**Disposition.** Delegated. The design decision is already made; this skill produces the plan. The `PLANNING.md` edit is committed through the `commit` skill, which classifies and queues it. Don't commit or implement from here — produce the plan and stop.

---

## Step 1: Snapshot the Claude Design session into the repo

The repo is canonical — work from a committed snapshot, never the live URL (`STANDARDS.md §15.2`). First check whether this session is already snapshotted (e.g. `design-update` may have imported it): look for the URL in `docs/mockups/SOURCES.md`. If there's an `active` row for it, use that existing snapshot and skip to Step 2.

Otherwise import it now (`STANDARDS.md §15.6` — the manual import workflow until a dedicated `claude-design-import` skill exists).

### Getting the right URL — use the "Hand off to Claude Code" button

**This is the correct path.** In Claude Design, the toolbar has a "Hand off to Claude Code" button. Clicking it opens a dialog showing a ready-made Claude Code command containing a **hand-off URL** at `https://api.anthropic.com/v1/design/h/<hash>?open_file=<filename>`. Seth typically copies just this URL and pastes it when invoking this skill. That URL is fetchable via the Anthropic API key.

**Do not confuse this with the browser address bar URL or the share link.** Those look identical in format but are authenticated via browser session cookie and will return 405/404 to curl. If it's unclear which Seth pasted, just try the fetch below — a successful download confirms it's the hand-off URL.

Once you have the hand-off URL, fetch and unpack:

```bash
mkdir -p /tmp/cd_extract
curl -s "HAND_OFF_URL_HERE" \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -o /tmp/cd.tar.gz
tar -xzf /tmp/cd.tar.gz -C /tmp/cd_extract
find /tmp/cd_extract -maxdepth 2 -type d   # inspect the top-level project dir
```

The bundle unpacks to:
```
<project-name>/
  README.md        ← read this first; explains the bundle structure
  chats/           ← design chat transcripts (intent + rationale)
  project/         ← HTML/CSS/JSX prototype files (ground truth)
```

### Fallback: "Download zip instead" option

The hand-off dialog also has a **"Download zip instead"** checkbox that downloads the bundle to disk. If the curl path above fails, ask Seth to check that option, locate the file (typically `~/Downloads/<project-name>.zip` or `.tar.gz`), and provide the path. Then extract:

```bash
mkdir -p /tmp/cd_extract && tar -xzf /path/to/bundle -C /tmp/cd_extract
# or for zip: unzip /path/to/bundle.zip -d /tmp/cd_extract
find /tmp/cd_extract -maxdepth 2 -type d
```

### Last resort: Chrome extension extraction

If neither the hand-off URL nor a local bundle is available (e.g. Seth shared an old project that predates the hand-off button), the Chrome extension can extract the design via the OmeletteService API from within an authenticated `claude.ai/design` session:

1. Navigate to `https://claude.ai/design` → find the project by name → open it.
2. Read the page accessibility tree (`read_page`) — the full chat transcript is visible in the sidebar and covers every screen, component, and decision.
3. Pull file content via `javascript_tool`:

```javascript
// List files
fetch('/design/anthropic.omelette.api.v1alpha.OmeletteService/ListFiles', {
  method: 'POST', headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({projectId: '<uuid>'})
}).then(r => r.json()).then(d => JSON.stringify(d))

// Fetch a file (returns base64-encoded content)
fetch('/design/anthropic.omelette.api.v1alpha.OmeletteService/GetFile', {
  method: 'POST', headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({projectId: '<uuid>', path: 'MyFile.html'})
}).then(r => r.json()).then(d => { window._b64 = d.content; return atob(d.content).length + ' chars'; })
```

Note: browser security filters may block reading raw decoded HTML that contains URL-like patterns. Workarounds: read CSS `<style>` blocks directly, extract class names / component names / data constants via regex, or parse with `new DOMParser()`.

### Snapshot: README-as-snapshot is valid

When only partial extraction is possible, a **comprehensive `README.md`** documenting everything extracted — CSS classes, component tree, state, tokens, UX flow — counts as the snapshot. The implementer needs the spec, not the raw prototype source. A well-structured README is more useful than an opaque file they can't run.

### Snapshot directory conventions

Then snapshot into the repo:

- **Path:** `docs/mockups/<YYYY-MM-DD>-<slug>/` — one dir per snapshot. Use today's date; `<slug>` is lowercase-kebab describing the session (`redesign-queue`, `add-goals`, `onboarding-flow`).
- Full bundle: **strip the archive's top-level project-name dir** so files land directly under the dated slug: `<slug>/README.md`, `<slug>/chats/`, `<slug>/project/`.
- Partial or README-only: the directory just has whatever you could extract plus a `README.md`.
- **Discard the `.tar.gz`** after extraction — the unpacked tree is the source of truth.
- **Append a row to `docs/mockups/SOURCES.md`** (newest first), status `active`. If this snapshot supersedes the current active mockup set, flip the previous row's status to `superseded` (`STANDARDS.md §15.5`).

```markdown
| <YYYY-MM-DD> | <slug> | <the pasted URL> | active | <one-line description> |
```

## Step 2: Understand the design and the product

Read the snapshot. What you have depends on how extraction went:

**Full bundle** (`project/` + `chats/` + `README.md`):
- The bundle's own **`README.md`** is authoritative for *how* to read it — including its instruction to **not** render the prototypes in a browser or take screenshots.
- **`project/`** (HTML/CSS/JS) is *truth* — read it directly for exact screens, states, layout, components, and tokens. This is what you're planning to build.
- **`chats/`** transcripts are *intent* — consult them when the source is ambiguous about why something is the way it is, or what behavior sits behind a static screen.

**Partial extraction or README-only snapshot**: Read the `README.md` you wrote — it is the truth for this snapshot. If gaps remain (ambiguous behavior, missing states), consult the live Claude Design session via the Chrome extension (`read_page` on the project URL surfaces the full chat history in the sidebar) before inventing scope.

Then build product context. Every fleet product carries the same canonical doc set in `docs/` (`STANDARDS.md §3`); read the ones relevant to what the design touches:

- **`docs/CLAUDE.md`** — per-product instructions, architecture, conventions. Read first.
- **`docs/PLANNING.md`** — existing milestones and their numbering (you'll append after the highest `M<n>`), plus the exact structure to match.
- **`docs/PRODUCT.md`** + **`docs/ARCHITECTURE.md`** — what exists today and how it works, so you can tell what the design *changes* vs. *adds*.
- **`docs/DECISIONS.md`** — the committed stack, so steps are written in the product's actual technology rather than assumed.
- **`docs/STYLE_GUIDE.md`** + **`docs/tokens.json`** — the current design system. If the new design diverges, that divergence is itself work (and a signal that `mockup-to-style-guide` should run too).
- **`docs/LIFECYCLE.md`** — stage and next milestone.

Then skim the source files the design will touch. The goal is to map *design surface → implementation work* in this product's stack: what data, endpoints, components, and wiring the design implies, and what already exists to reuse.

**Stay stack-agnostic.** Never assume a framework, language, or host — read `docs/DECISIONS.md`, `docs/CLAUDE.md`, and `docs/ARCHITECTURE.md` and plan in this product's terms. If the product uses a stack with a runbook in `docs/stacks/<stack>.md` (indexed in `FLEET_TECH.md §2.1`), consult it for platform-bootstrap steps.

If `docs/PLANNING.md` doesn't exist, this product hasn't reached Phase 4 — flag it rather than inventing a structure. `PLANNING.md` needs authoring first (start from `~/products/docs/templates/planning/PLANNING.md`).

## Step 3: Derive the milestone(s) and steps

Translate the design into ordered, concrete steps grouped under one or more **milestones**. A milestone is a *shippable increment* — when it closes, the product is deployable, usable, and verifiable. The implementer reads these steps literally on a fresh machine, so they must parse cleanly and carry enough detail to act on.

**Order within a milestone** follows the product's dependency chain — typically data/schema and backend before the UI that depends on them — but read `docs/CLAUDE.md` / `docs/ARCHITECTURE.md` for the product's own conventions rather than assuming.

**Each step gets explicit acceptance criteria inline**, because the implementer and the per-step verification gate check the step against them (`APP_DEV_PROCESS.md` Phase 5). Granularity is one sitting's work for an agent — not "build the redesign," not "change line 42."

- ✅ "Build the goal-progress component to match the snapshot's progress-bar treatment, using `STYLE_GUIDE.md` tokens; renders filled / empty / over-goal states."
- ✅ "Add the persistence field the new goal UI needs and expose it on the read endpoint, defaulting null."
- ❌ "Implement the new design." (no acceptance criteria possible)
- ❌ A full DDL + exact component diff. (that's implementation, not the plan)

**Mark human-only steps with `*(human)*` (`STANDARDS.md §4.2`).** The implementer agent **hard-blocks** on any step it can't do with its own tools — it won't skip ahead or guess a workaround — so steps needing Seth's manual action must be marked up front. As you write each step, ask: *can an agent do this with Bash / Read / Write / API calls using credentials Seth already provided?* If yes, leave it unmarked; if it needs a human, put `*(human)*` immediately after the checkbox. Mark only genuinely unworkable-by-agent actions:

- Account creation requiring identity verification (Apple Developer, App Store Connect, business banking)
- Payments needing a card or bank action
- Third-party dashboard config requiring SSO or interactive setup (creating a Sentry account, Stripe Connect onboarding, registering an OAuth app in a vendor UI)
- Waiting on external review (App Store review, legal review, regulator response)
- Physical actions (hardware, mail)

Do **not** mark anything an agent can do via tools, even if it touches a third-party system (editing config, running a CLI, hitting a vendor REST API with provided credentials, writing migrations). When in doubt, leave it unmarked — the implementer's runtime ambiguity flow is the safety net.

**Large designs — consider flag strategy inline.** If implementing the design spans 3+ steps or multiple sessions, plan how incomplete work merges safely as part of the steps (`APP_DEV_PROCESS.md` Phase 4): every flag gets an explicit removal condition and a matching cleanup step. There's no separate flag-planning skill in v1 — fold it into the milestone.

**Stack runbook gates.** If a step's acceptance criteria hit a platform-bootstrap moment (first device install, etc.) for a stack with a runbook in `docs/stacks/<stack>.md`, link the relevant runbook section, and transcribe any runbook items matching the `*(human)*` criteria as `*(human)*` sub-steps (`STANDARDS.md §4.2` "Stack runbooks").

Present the milestone(s) and step list to the user for a quick review — let them reorder, cut, or adjust — before writing to `PLANNING.md`. Keep this tight; the design is already decided, so this is a sanity check on the *plan*, not a redesign.

## Step 4: Append to `docs/PLANNING.md`

Add the work as a new milestone (or milestones), matching the canonical shape exactly (`STANDARDS.md §4.2`). The structure is a parsing contract — the implementer, `planning-compactor`, and the verify gate all key off it, so deviations break automation silently.

```markdown
## M<n> — <Milestone title>

**Goal:** <one paragraph — what this milestone unlocks.>

**Acceptance for milestone close:** <the observable state when this milestone is done.>

**Source:** docs/mockups/<YYYY-MM-DD>-<slug>/ (Claude Design snapshot)

### Steps

- [ ] **M<n>.1 — <Step title>.** <Acceptance criteria inline.>
- [ ] **M<n>.2 — <Step title>.** <Acceptance criteria.>
- [ ] *(human)* **M<n>.3 — <Step title>.** <Human-only action.>

### M<n> dependencies

- M<n-1>

### M<n> — what this does NOT do

(optional scope fence)
```

Heading rules that matter:

- **H2** is `## M<n> — Title`. The literal `M<n>` prefix is **load-bearing** — `## Phase N` and other shapes are non-conformant and the implementer will not see them. Number the new milestone(s) after the highest existing `M<n>`.
- **H3** is reserved for `### Steps`, `### M<n> dependencies`, `### M<n> — what this does NOT do`. Never `### Step N.M`.
- **Steps** are flat top-level checkboxes: `- [ ] **M<n>.<m> — Title.**` (with `*(human)*` before the bold ID when applicable). Nested bullets are allowed for work items, but the top-level checkbox is the unit of work.

Set each new milestone's **Source** to the snapshot path so the implementer can open the design ground truth. Insert after the last existing milestone unless the work is a prerequisite for something already planned (then place it before its dependents and fix the `dependencies` lines). Leave cross-milestone follow-up sections (`## Security follow-ups (<date>)`, `## MFB.1 — …`) where they are, and match the existing file's prose style.

After writing, confirm to the user:

```
Done — added Milestone M<n>: [name] to docs/PLANNING.md ([N] steps, [k] marked *(human)*),
sourced from docs/mockups/<YYYY-MM-DD>-<slug>/.
The implementer loop will pick up M<n>.1 on its next run. Commit the change (via the `commit` skill) to queue it.
```

The autonomous implementer reads `PLANNING.md` and selects the next unblocked step itself; the per-step verification gate drives the loop (`APP_DEV_PROCESS.md` Phase 5, `STANDARDS.md §9`). There is no "what's next" skill to run.

---

## Things to keep in mind

**The design is the source of truth, not your imagination.** Plan what the snapshot actually shows. If the design is ambiguous or implies behavior it doesn't depict (interactions, error states, data), check `chats/` first; if still unclear, surface the gap to the user rather than inventing scope.

**Reuse over reinvention.** Map design elements to existing components and services where you can — the snapshot may render something the product already has. Less work, more consistency.

**`PLANNING.md` is the handoff.** The steps you write are read literally by an autonomous agent with no memory of the design session. Clear titles, explicit acceptance criteria, correct `*(human)*` markers, a `Source` pointer to the snapshot, and conformant structure are what make the handoff work.
