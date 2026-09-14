---
name: agent-sync
description: Use when planning, verifying, or running agent profile synchronization between Claude Code, Codex, encrypted R2 storage, local stores, or trusted SSH-managed machines.
---

# Agent Sync

## Overview

Use this skill to operate `agent-sync` safely. Keep the default boundary at
`user-profile`: root commands handle active user instructions and bounded text
skill sources, not raw runtime settings, repository-owned agent policy, auth,
or session history. Use `--profile claude-portable` only when the user explicitly
wants portable Claude desired state and has reviewed its broader contract.

## First Checks

1. Work from the `agent-sync` repo when changing code:
   `/Users/reed/Code/agent-sync`.
2. Run `git status --short` before edits. Check the fetched default branch and
   existing worktrees before assuming this checkout has the newest features.
3. For operations, prefer the installed `agent-sync` after checking `agent-sync
   version`. Use `go run ./cmd/agent-sync` only from a checkout containing the
   reviewed implementation. For storage work, run:

```bash
agent-sync doctor --config ~/.agent-sync/config.json --verify-secrets
```

The config should contain refs only. Secret values belong in env, macOS
Keychain, or a future 1Password bridge.

## Workflows

For command recipes and safety gates, read `references/workflows.md`.

Use the matching section:

- `cc -> codex`: compare or materialize Claude Code user instructions into
  Codex `AGENTS.md`; add `--include skills` only when bounded user-level Claude
  Code skill sources should be copied into `$HOME/.agents/skills`.
- `local -> r2`: push encrypted user-profile bundles to R2.
- `r2 -> local/codex`: list, select the intended profile/prefix, pull with
  `--include skills` when skill restoration is intended, dry-run, then write
  with backup. Review every skipped artifact.
- `claude portable`: plan/push `claude-portable`, then pull to Claude with a
  dry-run; treat marketplace/plugin actions as a trust review, not executable
  bundle content.
- `API profiles`: keep definitions reference-only; require the separate key and
  `--include-secrets` for secret push/pull.
- `local -> ssh`: use skill-only rsync/scp recipes for trusted machines; do not
  add an SSH storage provider unless the user explicitly reopens that design.

## Inventory Reporting

- Lead with the logical package count, for example: `12 skill packages (83
  accepted files)`.
- Treat `files` and `parity-kind kind=skill-bundle` as diagnostic artifact
  counts, never as the number of skills.
- For mixed inventories, report the exact-name-deduplicated global package
  count first, followed by each provider's package/file breakdown.
- Build package lists from `parity-skill` lines. Do not infer the sync inventory
  from a raw recursive directory count, which can include excluded caches,
  virtual environments, hidden history, or secret files.
- Report `unattributed_files` explicitly when it is nonzero; do not invent
  package names from malformed source metadata.

## Safety Rules

- Never print API keys, sync keys, tokens, or `.env` contents.
- Never sync `~/.claude/projects/*`, sessions, transcripts, caches, auth files,
  or repo-owned `CLAUDE.md` / `AGENTS.md` through root sync commands.
- Never treat raw Claude `settings.json`, hooks, commands, agents, rules,
  output styles, or plugins as remote-safe. Only the `claude-portable`
  schema-aware outputs may enter its opt-in bundle.
- Never treat raw Codex `config.toml`, auth, logs, sessions, plugins,
  marketplaces, or plugin caches as remote-safe.
- Treat `$HOME/.agents/skills` as the Codex user skill root.
  `~/.codex/skills` is read-only legacy source compatibility; repo-local
  `.agents/skills` is project scope and must not enter a root bundle.
- Core skills include only `SKILL.md` and allowed UTF-8 text references.
  `claude-portable` may additionally include allowlisted, bounded source/scripts
  and selected small assets; dependency caches, hidden trees, secrets,
  symlinks, and unsupported extensions stay excluded.
- Never copy plugin checkouts or caches. Sync marketplace name/source and plugin
  ID/scope/version/commit pins only; installing them requires a separate review.
- Never put API keys in settings or `profiles.json`. Use explicit refs and keep
  the API-profile key distinct from the normal bundle key.
- Use exactly one of `--dry-run` and `--write`.
- Core skill text is not a complete package when scripts/assets are required.
  Review exclusions and restore trusted dependencies explicitly; never install
  an entrypoint alone and claim the skill works.
- Preserve shared instruction intent and provider/machine overlays. A Claude
  `@path` import or Workflow model rule needs native adaptation before Codex use.
- Prefer `--dry-run` before `--write`.
- Review add/update/unchanged, size, hash, source, and exclusion metadata before
  writing. Transform and pull use atomic replacement with backups for updates.
- Use isolated `--storage-prefix` values for smoke tests.
- Treat SSH as an operational recipe, not a core provider.
- Before claiming success, run the command that proves the exact path in
  question, such as `go test ./...`, `smoke`, `doctor --verify-secrets`, or
  `transform --dry-run`.
