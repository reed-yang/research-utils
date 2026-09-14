# Agent Sync Workflows

## Status Snapshot

Known working paths on this machine:

- `cc -> codex`: `transform --dry-run` on real homes; `--write` verified on
  temporary homes with recoverable atomic replacement and backup. Add
  `--include skills` to copy bounded user-level Claude Code skill text into
  `$HOME/.agents/skills`.
- `cc -> r2`: real R2 `smoke push/pull` passed.
- `cc+codex -> r2`: real R2 `smoke push/pull` passed.

Current secret refs:

- `keychain:agent-sync/sync-key`
- `keychain:r2/access-key-id`
- `keychain:r2/secret-access-key`

## Core Profile Contract

Root commands collect only active user instructions and bounded UTF-8 skill
sources (`SKILL.md` plus allowed files below `references/`). They do not collect
raw Claude settings/hooks/commands/agents/rules/output styles/plugins or raw
Codex config/auth/log/session/plugin state. Scripts, assets, binaries, symlinks,
special files, and repository `.agents/skills` are also excluded.

The opt-in `claude-portable` profile is a separate schema-aware contract. It
may include selected Claude settings fields, bounded custom source/scripts and
small assets, and sanitized marketplace/plugin desired state. It never includes
raw settings bytes, plugin checkouts/caches, auth, sessions, or project state.

Codex user skills live at `$HOME/.agents/skills`. The legacy
`~/.codex/skills` tree is a labeled source-compatibility path only. New
transforms never target that legacy tree.

## Claude Portable Multi-Machine Replacement

Inventory portable Claude state without a storage write:

```bash
agent-sync plan \
  --from claude-code \
  --to r2 \
  --scope user-profile \
  --profile claude-portable \
  --claude-home ~/.claude
```

Read the package-first inventory before the file-level diagnostics:

```text
parity-skills packages=12 files=83 unattributed_files=0
parity-skills-provider provider=claude-code packages=12 files=83
parity-skill package="agent-sync" providers=claude-code files=3
```

Report this as `12 skill packages (83 accepted files)`. Global package totals
deduplicate the same exact package name across providers, while provider lines
remain separate. Use `parity-skill` lines for the package list; never use a raw
recursive provider-directory file count as the sync inventory. Then review
included-kind and exclusion-reason parity counts, every blocked path, and the
scan budgets before pushing the portable bundle:

```bash
agent-sync push \
  --config ~/.agent-sync/config.json \
  --from claude-code \
  --scope user-profile \
  --profile claude-portable \
  --claude-home ~/.claude
```

On another machine, dry-run before restoring Claude-native state:

```bash
agent-sync pull \
  --config ~/.agent-sync/config.json \
  --to claude-code \
  --profile claude-portable \
  --dry-run \
  --claude-home ~/.claude
```

The plan includes marketplace names/sources and plugin IDs/version/commit pins.
It does not contain plugin files and does not install anything. Review source
trust, add/install through Claude Code, and then write the portable files:

```bash
agent-sync pull \
  --config ~/.agent-sync/config.json \
  --to claude-code \
  --profile claude-portable \
  --write \
  --claude-home ~/.claude
```

Matching-platform command/path overlays are merged; cross-platform overlays are
skipped. Existing target-only settings and local secret environment fields are
preserved. Changed files receive timestamped backups.

## Named API Profiles

Create `~/.agent-sync/profiles.json` with schema
`agent-sync.api-profiles.v1`. Keep endpoints/models in `env`; API-key/token
variables must use `secret_refs` with `env:`, `keychain:`, or restored `vault:`
refs. Never place a secret value directly in the file.

Inspect refs without resolving or printing values:

```bash
agent-sync profiles plan
```

The API-profile secret key must be a different 32-byte key from the ordinary
bundle key and should be configured as `secret_key_ref` in
`~/.agent-sync/config.json`. Secret transfer is always explicit:

```bash
agent-sync profiles push \
  --config ~/.agent-sync/config.json \
  --include-secrets

agent-sync profiles pull \
  --config ~/.agent-sync/config.json \
  --include-secrets \
  --dry-run

agent-sync profiles pull \
  --config ~/.agent-sync/config.json \
  --include-secrets \
  --write
```

Run a selected profile without printing its values:

```bash
agent-sync profiles exec --profile api-xb -- --version
```

Ordinary `pull` skips `secrets/api-profiles.json.enc`; only `profiles pull
--include-secrets` can decrypt and restore the encrypted local vault.

## Claude Code To Codex

Plan first:

```bash
agent-sync transform \
  --from claude-code \
  --to codex \
  --scope user-profile \
  --dry-run \
  --claude-home ~/.claude \
  --codex-home ~/.codex
```

Plan instructions plus skills:

```bash
agent-sync transform \
  --from claude-code \
  --to codex \
  --scope user-profile \
  --include skills \
  --dry-run \
  --claude-home ~/.claude \
  --codex-home ~/.codex
```

Write only after the user confirms:

```bash
agent-sync transform \
  --from claude-code \
  --to codex \
  --scope user-profile \
  --write \
  --claude-home ~/.claude \
  --codex-home ~/.codex
```

Write instructions plus skills only after reviewing the dry-run:

```bash
agent-sync transform \
  --from claude-code \
  --to codex \
  --scope user-profile \
  --include skills \
  --write \
  --claude-home ~/.claude \
  --codex-home ~/.codex
```

Dry-run reports add/update/unchanged, target size, existing/desired SHA-256,
source provider, and exclusion reasons without printing content. `--write`
uses the same containment-checked atomic writer for instructions and skills. It
backs up an existing `~/.codex/AGENTS.md` under
`~/.codex/.agent-sync-backups/`; skill backups live beside the official target
under `$HOME/.agents/skills/<name>/.agent-sync-backups/`. Unchanged content
causes no write and no backup.

Verify Codex sees copied skills without starting an interactive session:

```bash
codex debug prompt-input 'list visible skills' > /tmp/codex-prompt.json
rg 'paper-ingestion|research-paper-writing|agent-sync' /tmp/codex-prompt.json
```

## Local Or Profile To R2

Verify refs:

```bash
agent-sync doctor --config ~/.agent-sync/config.json --verify-secrets
```

Use an isolated prefix for smoke:

```bash
prefix="agent-sync/v1/smoke/manual-$(date +%Y%m%d-%H%M%S)/"
agent-sync smoke \
  --config ~/.agent-sync/config.json \
  --scope user-profile \
  --storage-prefix "$prefix" \
  --claude-home ~/.claude \
  --codex-home ~/.codex
```

Push durable user-profile bundles:

```bash
agent-sync push \
  --config ~/.agent-sync/config.json \
  --from claude-code,codex \
  --scope user-profile
```

List encrypted objects without decrypting:

```bash
agent-sync list --config ~/.agent-sync/config.json
```

## R2 To Codex

Always dry-run first:

```bash
agent-sync pull \
  --config ~/.agent-sync/config.json \
  --to codex \
  --dry-run
```

Write only after confirming the target and backup behavior:

```bash
agent-sync pull \
  --config ~/.agent-sync/config.json \
  --to codex \
  --write
```

Pull uses the same add/update/unchanged metadata and recoverable atomic writer
as transform. A failed temporary write or rename leaves the original intact.

## Trusted SSH Recipe

Do not add an SSH storage provider for ordinary use. For controlled machines,
move encrypted local bundles with `rsync` or `scp`, then run `agent-sync` on the
remote host.

Create a local encrypted store:

```bash
agent-sync push \
  --from claude-code,codex \
  --to local \
  --storage-root ./agent-sync-store \
  --scope user-profile \
  --key "$AGENT_SYNC_KEY"
```

Copy the encrypted store:

```bash
rsync -av ./agent-sync-store/ user@host:~/.agent-sync/store/
ssh user@host 'chmod 700 ~/.agent-sync ~/.agent-sync/store && find ~/.agent-sync/store -type f -exec chmod 600 {} +'
```

On the remote host, dry-run before writing:

```bash
agent-sync pull \
  --from local \
  --storage-root ~/.agent-sync/store \
  --to codex \
  --dry-run \
  --key "$AGENT_SYNC_KEY"
```

Then, only after confirmation:

```bash
agent-sync pull \
  --from local \
  --storage-root ~/.agent-sync/store \
  --to codex \
  --write \
  --key "$AGENT_SYNC_KEY"
```

For a fully trusted machine where encryption is unnecessary, a direct `rsync`
of `~/.codex/AGENTS.md` and `$HOME/.agents/skills/` can be acceptable, but treat
that as an explicit manual operation rather than agent-sync core behavior.
Never include `auth.json`, sessions, logs, raw settings, or plugin caches in
this recipe.

## Trusted Codex CLI Bootstrap

For trusted Linux x86_64 nodes that already have a synced `~/.codex/AGENTS.md`
and `$HOME/.agents/skills/`, the Codex npm package contains a native binary that can
run without Node being installed on the target node. This is useful for clusters
where installing Node/npm everywhere is slow or unavailable.

Find the native binary on a working node:

```bash
codex_bin="$HOME/.npm-global/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex"
"$codex_bin" --version
```

Copy it to a trusted peer:

```bash
ssh peer 'mkdir -p ~/.local/bin'
rsync -av "$codex_bin" peer:/home/siyuan/.local/bin/codex
ssh peer 'chmod 755 ~/.local/bin/codex && ~/.local/bin/codex --version'
```

Verify the profile can be read without starting an interactive session:

```bash
ssh peer '~/.local/bin/codex debug prompt-input "check agent-sync skill" >/tmp/codex-prompt.json && rg agent-sync /tmp/codex-prompt.json'
```

Keep raw `config.toml`, `auth.json`, sessions, logs, and plugin state outside
Agent Sync and this CLI bootstrap workflow. Validate referenced secrets without
printing values.


## Codex Skill Recovery and Dependency Checks

Use `pull --to codex --include skills --profile core --dry-run` to restore
bounded core skill text to `$HOME/.agents/skills`. Without `--include skills`,
skill artifacts are explicitly reported as skipped. With a custom Codex home,
also pass `--codex-skills-root "$HOME/.agents/skills"`.

To restore reviewed scripts and small assets already present in an explicitly
selected Claude portable bundle, use `--profile claude-portable --include
skills`. This restores portable skill sources into Codex; Claude settings and
plugin/agent/command artifacts without a Codex mapping are reported as skipped.
The provider-specific instruction content still requires semantic review.
Executable metadata is preserved as owner-only mode 0700. Conflicting skill
contents or modes in a bundle fail before writes; identical duplicates dedupe.

Core collection excludes scripts and assets. Inspect package exclusions before
claiming recovery is complete. For example, atom-commit requires its attribution
resolver alongside SKILL.md. Do not copy environments, caches, credentials, or
plugin checkouts to fill these gaps. Use the reviewed source package or a
trusted, explicitly selected file transfer, retaining target-local environments.

Both transform and pull reject simultaneous `--dry-run --write`. Always inspect
the plan first and then invoke only `--write` within existing user authorization.

When a prefix has several bundles, `pull` refuses to render until `--object-key`
selects one exact key from `list`. Profile filters alone may not disambiguate
multiple core bundles. `status --from claude-code,codex` exports IDs/hashes;
`status --from claude-code,codex --against snapshot.json` compares without writes.
