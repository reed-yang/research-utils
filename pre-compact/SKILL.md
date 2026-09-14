---
name: pre-compact
description: Prepare a Claude Code session for context compaction by reconciling all meaningful work into canonical project documentation, creating a resumable checkpoint under logs/session-ckpts/, and producing a paste-ready post-compact resume prompt. Use when the user says pre-compact, asks to save session state before /compact or a handoff, or needs motivations, decisions, discoveries, progress, validation, blockers, tracking, and resume pointers made complete before context is reset.
---

# Pre-Compact

Leave the project in a state that a fresh session can resume without relying on chat history. Update durable project knowledge first, then create a concise session checkpoint that points to it.

## Invariants

- Treat canonical project documents as the source of truth; never move durable knowledge only into a session checkpoint.
- Account for every meaningful requirement, motivation, decision, discovery, change, validation result, blocker, and next action from the current session.
- Update an existing owner document before creating a new one.
- Keep checkpoints concise and pointer-oriented. Do not duplicate entire plans, reports, diffs, transcripts, or command output.
- Preserve repository conventions, user edits, and unrelated formatting.
- Never record secrets, credentials, tokens, private environment values, or sensitive raw output.
- Store the same task-specific post-compact resume prompt in the checkpoint and the final report.
- Do not invoke `/compact`, commit, push, archive, or delete files unless the user separately requested it.

## Workflow

### 1. Discover the State Owners

1. Identify the workspace root and read applicable `CLAUDE.md` and `AGENTS.md` files.
2. Inspect the current conversation, active todo list, and any plan named in the session.
3. Search for existing state documents before inventing new ones:
   - `README.md`, `docs/`, `docs/plans/`, `task_plan.md`
   - `findings.md`, `progress.md`, `logs/findings.md`, `logs/progress.md`
   - design records, decision logs, issue references, and task-specific reports
4. Inspect `git status`, staged and unstaged diffs, and recent commits when Git is available. Handle an unborn branch or non-Git directory without failing.
5. Inspect relevant validation output and generated artifacts from the session. Do not trust a remembered success when the underlying command or artifact does not support it.

Use `rg --files` and targeted reads. Do not scan large generated, dependency, cache, or secret-bearing directories merely to claim completeness.

### 2. Build a Coverage Ledger

Before editing, enumerate the meaningful session state under these headings:

- user requirements and success conditions
- original motivation and why the chosen direction matters
- constraints and user preferences
- decisions and their rationale
- durable discoveries and corrected assumptions
- files or systems changed
- completed work and current in-progress work
- validation performed and exact outcomes
- blockers, risks, open decisions, and failed approaches that affect resumption
- remaining work and the first executable next action

Assign each item to a canonical destination. Read `references/checkpoint-format.md` for the routing table and checkpoint contract.

### 3. Reconcile Canonical Documentation

Update documents in ownership order:

1. Update the active plan or tracker so statuses reflect actual work, not intent.
2. Record durable discoveries, caveats, and corrected assumptions in the existing findings or decision document.
3. Record completed work and verification evidence in the existing progress log.
4. Update task-specific design, architecture, research, or operational documents when their conclusions changed.
5. Update `CLAUDE.md` only when durable repository guidance or architecture changed; update `README.md` only when user-facing behavior or usage changed.

Create a new canonical document only when no existing document has the correct scope. Give it one clear responsibility and link it from the active plan, index, or other discoverable owner. Do not create a generic context dump.

When current evidence contradicts an older dated record, preserve history and add a dated correction or supersession note. Do not silently rewrite history.

### 4. Prove Documentation Coverage

Re-read every changed document and compare it against:

- the coverage ledger
- the user's explicit requests
- the active plan or todo state
- the actual working-tree diff and artifacts
- validation commands and their observed results

Resolve contradictions and missing destinations before continuing. Any meaningful item that remains only in chat is a coverage failure. If an ambiguity cannot be resolved safely, record it explicitly as an open decision with the evidence needed to resolve it.

### 5. Create the Session Checkpoint

Only after canonical documents are current:

1. Create `logs/session-ckpts/` if it does not exist.
2. Generate a UTC timestamp with `date -u +%Y-%m-%dT%H%M%SZ`.
3. Write one new file named `<timestamp>--<task-slug>.md`; use lowercase hyphen-case for the slug and append a numeric suffix on collision.
4. Follow `references/checkpoint-format.md` exactly enough that a fresh session can locate the state owners and resume in order.
5. Link the previous checkpoint from the same continuing task or session as `Supersedes` when identifiable. Never delete or overwrite an older checkpoint merely because a new one exists.
6. Add a `Post-Compact Resume Prompt` containing the exact checkpoint path and the first safe continuation behavior.

The checkpoint may contain session-specific recovery state, but new durable facts discovered while drafting it must first be written to their canonical document. Use repository-relative paths in pointer tables and record the absolute workspace root once near the top.

Write the resume prompt in the user's language unless requested otherwise. Make it self-contained: state that compaction has completed, tell the new context to read the checkpoint and every canonical document it points to, reconcile the saved state against current files, recover motivation and tracking, avoid repeating completed work, and continue from the first remaining executable action. If the task is complete, tell it to confirm the recovered state and wait for the user's next instruction.

### 6. Verify and Report

Verify that:

- every checkpoint pointer exists or is clearly marked as external
- plan and progress statuses agree with the working tree
- validation claims include the command or artifact that supports them
- remaining work has an ordered, executable first step
- the checkpoint contains no secrets, transcript dump, or unsupported claim
- `git status` is accurately summarized, including untracked or staged files
- the post-compact resume prompt contains the exact checkpoint path, no placeholders, and continuation behavior consistent with the recorded task state

Report the canonical documents updated or created, the checkpoint path, unresolved items, and whether the session is ready for manual compaction. End every successful report with a `Post-Compact Resume Prompt` heading followed by one fenced `text` block containing the exact prompt stored in the checkpoint. Put no explanatory text inside that block so the user can copy it directly after `/compact`.

If coverage is incomplete, say so, do not claim readiness, and do not emit a prompt that falsely says the context is ready to resume.
