---
name: summary
description: This skill should be used when providing concise, priority-first summaries of research papers. Use for quickly understanding what a paper does, what is genuinely new, how it works, what the main results are, and whether the limitations materially weaken the conclusion.
---

# Paper Summary Skill

Generate priority-first summaries of research papers with keyword extraction. Read ingested paper markdown, write a concise and decision-useful summary to `notes.md`, and backfill extracted keywords into YAML frontmatter tags.

## When to Use This Skill

- Summarizing a research paper after ingestion for quick understanding
- Extracting the paper's main claim, novelty, method, and main results
- Preparing compact research notes for later recall
- Generating keywords for indexing and retrieval
- As a follow-up step after `paper-ingestion`

## Pipeline Context

Typical workflow: `paper-ingestion` -> **`summary`** -> `paper-translate` / `paper-validator`

Input: The `markdown_path` returned by `paper-ingestion`, normally `full_text-{paper_name}.md`, or its paper directory. Legacy `full_text.md` is also supported.

## Default Behavior

By default, produce a **priority-first full-paper summary** only.

The summary should help the reader answer, as quickly as possible:

- What does this paper do?
- What is the main claim?
- What is genuinely new?
- What is the motivation behind the method design?
- How does the method actually work?
- What are the main results?
- Do the limitations materially weaken the conclusion?

Do **not** produce section-by-section summaries.

## Language Settings

- **Default output language**: Chinese (中文)
- Can be specified as English by the user
- **Chinese mode rules**:
  - Technical terms and proper nouns remain in English when appropriate
  - Paper titles remain in original language
- **Keywords are always in English** regardless of output language

## Reading Strategy

Do not summarize the paper in section order.

Use a priority-first reading strategy:

1. Read abstract, introduction, conclusion, and the main result tables/figures first
2. Identify the core claim, novelty, motivation, method shape, and strongest support
3. Drill into method, main experiments, and ablations only as needed
4. Allocate summary space by importance, not by section order

By default, do **not** spend summary budget on:

- References
- Appendix
- Prompt details
- Long tables item-by-item
- Related work survey details
- Future work, unless it clarifies an important limitation

## Workflow

### Step 1: Locate Paper Files

- Prefer the supplied original Markdown path or ingestion JSON's `markdown_path`.
- For a directory, locate its single `full_text-{paper_name}.md` or legacy `full_text.md`. Exclude translated files ending in lowercase two-letter suffixes such as `_ch.md` or `_zh.md`. If multiple originals remain, ask which one to use instead of guessing.
- Check for matching `<original_stem>_ch.md` or `<original_stem>_zh.md` translations (for keyword backfill only).

### Step 2: Read Paper Content

- Read the resolved original Markdown only.
- Do not use a translated Markdown file as the source for summarization.

### Step 3: Generate Summary

Write a concise priority-first summary using the format below.

The summary should emphasize importance, novelty, motivation, mechanism, and main support, not uniform coverage.

### Step 4: Extract Keywords

- Extract at least 4 keywords from the paper (typically 4-8)
- Follow the keyword rules below
- Include keywords in the summary output

### Step 5: Write Summary to notes.md

- Save the complete summary output into `notes.md` in the same paper directory
- Replace placeholder note content if present

### Step 6: Backfill Keywords to Tags

- Append extracted keywords to the `tags:` list in the resolved original's YAML frontmatter.
- If its matching Chinese translation exists, append the same keywords there too.
- Do not modify any content outside YAML frontmatter

## Output Format

```md
# Paper Summary: {Title}

## Takeaway
[1-3 sentences. State what the paper does and why it matters.]

## Core Claim
[1-3 sentences. State the main claim or thesis of the paper.]

## What's New vs Prior Work
[1-4 sentences. Explain the genuine novelty and what is stronger or different than recent work.]

## Method
[Usually 2-4 short natural paragraphs, not a bullet list. Explain the method from first principles.
Start from the problem pressure, failure mode, or design motivation that makes the method necessary.
Then explain the core idea, the main components or stages, the key objective or optimization target if important,
the training or inference flow when central, and why these design choices should produce the claimed gains.
This should usually be the most detailed section of the summary.
Prefer causal explanation and paper storyline over module inventory.
It is acceptable to use multiple paragraphs if that makes the logic clearer.
Be detailed but not verbose, and omit appendix-only variants or low-importance implementation detail.]

## Key Results
[2-5 sentences. Summarize the most important results and the main comparison points.
Focus on the outcomes that matter most for the paper's claim; do not list every benchmark.]

## Limitations
[1-2 sentences. Keep this brief, and note whether the limitations materially weaken the main conclusion.]

## Historical Impact (conditional — include ONLY when criteria in Summary Requirements are met)
[2-5 sentences. Describe how this work influenced subsequent research:
what paradigm it established, what methods or techniques it introduced that became widely adopted,
or what research directions it opened. Focus on concrete downstream impact, not speculation.]

## Keywords
keyword1, keyword2, keyword3, keyword4, ...
```

## Summary Requirements

- **Priority-first**: Allocate space by importance, not by section order
- **Decision-useful**: Help the reader quickly understand the claim, novelty, motivation, mechanism, and whether the paper is convincing
- **Method-first detail**: `Method` should usually be the most detailed section in the summary
- **First-principles method**: Explain the method from motivation -> core idea -> mechanism -> why it should work
- **Narrative clarity**: Preserve the paper's storyline; explain what problem pressure leads to each major design choice
- **Mechanism over inventory**: Explain how the method works and why the main design choices matter; do not turn `Method` into a catalog of components
- **Multiple paragraphs allowed**: Use 2-4 short paragraphs in `Method` when needed for clarity; prefer natural prose over bullet inventories
- **Concise**: Do not rewrite the full paper in compressed form
- **Technically accurate**: Preserve precise terminology and comparisons
- **Novelty-focused**: Clearly separate what the paper does from what is actually new
- **Results-focused**: Summarize the strongest outcomes, not every experiment
- **Historical Impact gate**: Include the `Historical Impact` section ONLY when ALL of: (a) you have confident knowledge that this paper's method, framework, or idea was adopted or extended by multiple subsequent works, (b) you can name concrete examples of the downstream influence (named methods, paradigm shifts, standard components). Omit the section entirely when: the paper is very recent (published within ~6 months), is an incremental improvement on existing methods, or you lack sufficient knowledge about its downstream influence. When in doubt, omit.

## Keyword Extraction Guidelines

- **Quantity**: At least 4, typically 4-8
- **Coverage**: Prefer a mix of domain, method, technique, and task keywords
- **Priority**: Paper's own keywords > abstract concepts > core terms from full text
- **Format**: English, lowercase except proper nouns and standard abbreviations
- **No spaces in tags**: Use hyphens (`-`) for multi-word tags

Examples:

- `test-time-training`
- `diffusion-transformer`
- `single-cell-analysis`
- `kernel-engineering`

## Keyword Backfill Rules

Backfill means appending extracted keywords into the `tags:` list in the resolved original's YAML frontmatter (and its matching `_ch.md` or `_zh.md` translation if present). Insert keywords before the `aliases:` line.

If `tags:` already contains entries beyond `paper`, assume keywords were already backfilled and skip.

Do not modify content outside YAML frontmatter.

## Important Constraints

- Do not introduce information not present in the source text
- Do not editorialize beyond evidence-based judgment about whether limitations weaken the conclusion
- Do not summarize the paper section-by-section
- Do not spend space on appendix/reference material unless essential to the core claim
- Maintain technical precision appropriate for an academic audience
- Only backfill keywords when producing a full paper summary
