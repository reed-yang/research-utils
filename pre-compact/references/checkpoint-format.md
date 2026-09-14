# Pre-Compact Routing and Checkpoint Contract

Read this file before writing a pre-compact checkpoint.

## Contents

- Canonical routing and ownership
- Checkpoint filename and required template
- Post-compact resume prompt contract
- Quality rules

## Canonical Routing Table

| Information | Preferred owner | Checkpoint treatment |
|---|---|---|
| Repository-wide rules and durable architecture | Applicable `CLAUDE.md` or architecture document | Point to the exact path and relevant section |
| User-visible setup or usage | Existing `README.md` or user guide | Point only; do not restate the guide |
| Active scope, work breakdown, and task status | Existing plan, todo, issue, or tracker | Point to it and summarize only the current boundary |
| Durable technical findings and corrected assumptions | Existing findings, research, decision, or design document | Point to the conclusion and preserve any unresolved caveat |
| Completed work and verification evidence | Existing progress log or task report | Point to the dated entry and summarize the latest verified state |
| Detailed design or analysis with independent value | Existing task-specific document; create one only if no owner exists | Point to the document and explain why it matters for resumption |
| Code and artifact state | Source paths, diff, tests, generated artifacts | List exact paths and symbols/artifacts needed to continue |
| Session identity, compact boundary, immediate next command, pointer ordering, and paste-ready resume prompt | New session checkpoint | Record directly because these are handoff metadata |

Do not create parallel `context.md`, `notes.md`, or `handoff.md` files when an existing owner already serves that role.

## Filename

Use:

```text
logs/session-ckpts/<YYYY-MM-DDTHHMMSSZ>--<task-slug>.md
```

Examples:

```text
logs/session-ckpts/2026-07-23T221530Z--pre-compact-skill.md
logs/session-ckpts/2026-07-23T231010Z--retrieval-index-fix-2.md
```

## Required Checkpoint Template

Use the following headings. Omit a subsection only when it is genuinely not applicable; write `None` for blockers or open decisions when that absence is important.

````markdown
# Session Checkpoint: <short task name>

- Created (UTC): `<ISO-8601 timestamp>`
- Workspace root: `<absolute path>`
- Session ID: `<current Claude session ID when available; otherwise unavailable>`
- Branch / revision: `<branch and commit, unborn branch, non-Git directory, or unavailable>`
- Working tree: `<clean/dirty plus staged, unstaged, and untracked summary>`
- Supersedes: `<relative checkpoint path or None>`

## Resume First

1. Read `<policy path>`.
2. Read `<active plan or tracker path>` and continue from `<specific item>`.
3. Read the canonical documents in the priority order below.
4. Inspect `<specific diff, source path, artifact, or command>` before editing.
5. Execute `<first safe next action>`.

## Task and Motivation

- Goal: <current outcome>
- Original motivation: <why the work was started and what problem it solves>
- Success condition: <observable completion condition>
- Constraints: <important user or repository constraints>

## Current State

### Completed

- <verified completed item with canonical pointer>

### In Progress

- <partially completed item and exact stopping point>

### Remaining

1. <ordered next item>

### Blockers and Open Decisions

- <blocker, owner, required evidence, or None>

## Canonical Document Map

| Priority | Path | Section or role | Why read it | Current state |
|---|---|---|---|---|
| 1 | `<relative path>` | `<heading/owner>` | `<resume relevance>` | `<updated/unchanged/new>` |

## Working State

### Code and Artifact Pointers

| Path | Symbol, section, or artifact | State / reason |
|---|---|---|
| `<relative or explicit external path>` | `<symbol/section>` | `<changed, generated, read-only, or pending>` |

### Working-Tree Details

- Staged: <paths or None>
- Unstaged: <paths or None>
- Untracked: <paths or None>
- User-owned or unrelated changes to preserve: <paths or None>

### Verification

| Command or artifact | Result | What it proves |
|---|---|---|
| `<exact command or path>` | `<pass/fail/not run plus concise evidence>` | `<bounded claim>` |

## Tracking and Decision Pointers

- Active plan/tracker: `<path and current checklist item>`
- External issue/PR/task IDs: `<IDs/URLs or None>`
- Key decision rationale: `<path and section>`
- Failed approaches that must not be repeated: `<pointer or None>`

## Resume Guardrails

- <state that must be preserved>
- <assumption that requires re-verification>
- <action that requires user authorization>

## Coverage Audit

| Source checked | Meaningful information routed to |
|---|---|
| Current user requirements and conversation | `<canonical paths>` |
| Active plan/todos | `<canonical paths>` |
| Git status/diff or non-Git state | `<canonical path or this checkpoint for transient state>` |
| Validation results and artifacts | `<canonical paths>` |

## Not Persisted

- <irrelevant, sensitive, redundant, or unavailable context and why it was excluded; or None>

## Post-Compact Resume Prompt

```text
Context compaction has just completed. Read the session checkpoint at `<absolute checkpoint path>`, then follow its `Resume First` and `Canonical Document Map` sections to read the relevant project documents and inspect the current working state. Recover the task's motivation, constraints, decisions, tracking, completed work, validation evidence, blockers, and next actions. Reconcile the checkpoint against the current files and latest verification, do not repeat completed work, and continue from `<first remaining executable action or completed-task behavior>`.
```
````

## Post-Compact Resume Prompt Contract

Write one task-specific prompt in the user's language unless requested otherwise. Store it under `Post-Compact Resume Prompt` in the checkpoint and repeat it verbatim in the successful pre-compact final report.

The prompt must:

- say that context compaction has just completed, because it is intended to be pasted afterward
- include the exact absolute checkpoint path, not a glob, directory, or placeholder
- tell the new context to read `Resume First`, the canonical document map, and the relevant working-state pointers
- recover the original motivation, constraints, decisions, tracking, progress, verification, blockers, and next actions
- require comparison with current files and the latest verification so stale checkpoint details do not override reality
- prevent re-planning and repetition of completed work
- name the first remaining executable action when one exists
- tell the model to confirm recovery and wait for the user's next instruction when the task is already complete

Keep the prompt compact enough to paste as one block. Do not include pre-compact commentary, markdown outside the fenced block, or instructions to run `/compact` again.

## Quality Rules

- Keep the checkpoint as an index and delta, normally below 200 lines.
- Make the first resume action executable; avoid vague instructions such as “continue working.”
- Use exact paths. Add headings, symbols, issue IDs, or artifact names when a path alone is ambiguous.
- Distinguish `completed`, `in progress`, `not started`, `blocked`, `not run`, and `unverified` precisely.
- Summarize a dirty working tree by ownership and relevance. Never imply that every dirty file belongs to the current task.
- Bound validation claims to what a command actually proves. A syntax check is not an integration test.
- Record the original motivation and current success condition even when the implementation direction changed.
- Prefer pointers to dated findings/progress entries over duplicating their prose.
- If a required canonical document is missing, create or update that document before finishing the checkpoint.
- If a pointer is external, label it `external` and include enough location information to recover it without exposing credentials.
- Keep the checkpoint and final-report resume prompts identical and free of unresolved placeholders.
