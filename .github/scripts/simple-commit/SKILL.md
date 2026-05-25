---
name: simple-commit
description: "A lightweight alternative to the full commit skill for small, low-stakes changes that don't need risk classification, queue routing, or adversary review. Use this when the user says things like 'quick commit', 'simple commit', 'just commit this', 'just ship it', 'small change — just commit', 'don't need the full commit flow', or anything that signals they want a fast no-ceremony commit-and-PR. Also use for obviously trivial changes (typos, config tweaks, README edits, adding a file) where invoking the full commit machinery would be overkill. If the change touches auth, payments, PII, or migrations — use the full commit skill instead."
---

# Simple Commit

Fast path: branch (if needed) → commit all changes → push → open PR against main. No risk classification, no adversary loop, no queue routing.

Use this for small, obviously safe changes. If you're unsure whether something qualifies, use the full `commit` skill instead.

---

## When NOT to use this skill

Stop and use the full `commit` skill if the diff touches:
- Auth, session handling, or access control
- Payments or financial data
- PII or user data
- Database migrations
- Anything marked in `docs/CLAUDE.md` under `## Must Escalate`

---

## Steps

### 1. Check the diff

```bash
git status
git diff --stat
```

If there are no changes, tell the user and stop.

If there are untracked files that look unintentional (build artifacts, `.env` files, secrets), list them and ask before including.

### 2. Branch if on main

Check the current branch:

```bash
git branch --show-current
```

If on `main` (or `master`), create a new branch with a short kebab-case name derived from the change:

```bash
git checkout -b <branch-name>
```

Good branch names: `fix-typo-in-readme`, `update-nav-color`, `add-favicon`, `bump-timeout`. Keep them under ~40 characters.

If already on a feature branch, stay on it.

### 3. Commit all changes

Stage everything and commit:

```bash
git add -A
git commit -m "<message>"
```

**Writing the commit message:** Look at the diff and write a short imperative summary of what changed and why. Check `git log --oneline -5` first — if the repo uses conventional commit prefixes (`feat:`, `fix:`, `chore:`, etc.) follow that style; otherwise use plain imperative.

Good: `fix: correct typo in onboarding copy`, `chore: add vending.json to skill folders`, `update logo to new brand color`
Not great: `made some changes`, `updates`, `wip`

One line is usually enough. Add a second line only if the why is genuinely non-obvious.

### 4. Push and open PR

```bash
git push -u origin <branch-name>
gh pr create --base main --title "<commit message summary>" --body "$(cat <<'EOF'
## Summary
<One sentence describing what this PR does.>

🤖 simple-commit
EOF
)"
```

Capture and show the PR URL to the user.

That's it — done.

---

## Error handling

| Situation | What to do |
|---|---|
| No changes | Tell the user, stop. |
| Suspicious untracked files | List them, ask before staging. |
| Already on a feature branch | Commit there without creating another branch. |
| `gh` not available | Push and construct the PR URL from `git remote get-url origin`, present it as a link. |
| Push rejected | Run `git pull --rebase origin <branch>` then re-push. |
