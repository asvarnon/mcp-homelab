---
applyTo: "**"
---

# Agent Workflow Instructions

These rules apply to every interaction in this workspace. They are non-negotiable and override default agent behavior.

## Session Start (do this before anything else)

1. Run `memory view /memories/session/` to list session files
2. Read the most recent session file relevant to the current task before taking any action
3. Read `/memories/repo/versioning.md` before any release, deployment, or version-related work
4. Only then proceed with the user's request

## Session Notes (during the session)

- Write to `/memories/session/` after each significant action — not at the end
- Use one rolling file per topic/task (e.g., `session-v1.5.0.md`, `dhcp-backend.md`)
- Record: decisions made, files modified, commands run, open questions, pending steps
- If a session file for the current topic already exists, update it — don't create a duplicate

## Promote Findings Immediately

- When something permanent is discovered (a correct process, a wrong assumption, a repo convention), write it to `/memories/repo/` or `/memories/user-preferences.md` **immediately** — not at end of session
- Do not rely on session compaction to preserve important facts

## Release / Publish Checklist

Before any PyPI publish, package build, or version tag operation:

1. Check `.github/workflows/` for an existing release workflow
2. If a workflow exists: push a `vX.Y.Z` tag — the workflow handles everything
3. **NEVER run `twine upload` or `python -m build` + upload directly** — this bypasses Trusted Publishing and triggers a PyPI security warning
4. The publish workflow (`publish-release.yml`) has `skip-existing: true` — re-tagging is safe

## Code Authorship

- **NEVER write or edit Python code directly** — always delegate to Codex Agent via `runSubagent`
- This includes: new files, edits to existing Python files, test files, config changes in Python projects
- Orchestrator role only: plan, scope, review, validate tests, manage git

## Git Workflow

- Feature/fix branches only → PR → merge
- **NEVER push directly to master** — no exceptions, even for "just a config file"
- CI workflow (`publish-release.yml`) uses branch protection — direct pushes will warn
