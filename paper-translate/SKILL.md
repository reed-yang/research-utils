---
name: paper-translate
description: Translate research paper markdown files to Chinese while preserving LaTeX, code, images, and formatting. Use after paper-ingestion to translate an ingested paper.
---

# Paper Translation Tool

Translate research paper markdown files (from paper-ingestion) to Chinese while preserving all formatting.

Use the ingestion result's actual `markdown_path`, including descriptive names
such as `full_text-Faster-WAM.md`. The translator preserves the input stem and
appends the language suffix: Chinese produces `full_text-Faster-WAM_ch.md`.
Legacy `full_text.md` inputs continue to work. When given only a directory,
select its single original, excluding lowercase two-letter translation suffixes;
ask which original to use if the directory is ambiguous.

## Environment Setup

```bash
cd paper-translate
uv sync
```

API keys are loaded from `paper-translate/.env`:

```env
DEEPSEEK_API_KEY=sk-...
TENSORBLOCK_API_KEY=tb-...
```

## Quick Reference

```bash
cd paper-translate

# Translate using TensorBlock (default)
uv run scripts/translate_paper.py /path/to/paper_folder/full_text.md

# Translate using DeepSeek
uv run scripts/translate_paper.py /path/to/paper_folder/full_text.md --backend deepseek

# Specify target language (default: Chinese)
uv run scripts/translate_paper.py /path/to/paper_folder/full_text.md --target-lang Japanese
```

## Backend Selection

| Backend | API | Notes |
|---------|-----|-------|
| `tensorblock` (default) | TensorBlock Forge API | Alternative backend |
| `deepseek` | DeepSeek API | High quality, fast |

## What Gets Translated

| Element | Translated | Notes |
|---------|------------|-------|
| Body text | Yes | Main content |
| Headings | Yes | All heading levels |
| YAML frontmatter | No | Preserved, language tag added |
| LaTeX formulas | No | `$...$`, `$$...$$` preserved |
| Code blocks | No | Fenced and inline code preserved |
| Image references | No | Paths preserved exactly |
| Link URLs | No | URLs preserved, text translated |
| HTML tables | No | Structure preserved |
| Citations | No | `[1]`, `[17]` preserved |

## Output Structure

The translated file is saved alongside the original:

```
20260202-Paper_Title/
  reference.pdf        # Original PDF
  full_text-Faster-WAM.md    # Original markdown
  full_text-Faster-WAM_ch.md # Translated markdown
  notes.md             # Notes file
  assets/              # Images (unchanged)
```

## JSON Output

**Success:**
```json
{"status": "success", "output_path": "...", "backend": "tensorblock", "target_lang": "Chinese"}
```

**Error:**
```json
{"status": "error", "message": "..."}
```

## Resume from Cache

Long papers may fail due to API errors.  Use `--resume` to cache successful chunks and resume from where you left off:

```bash
# First attempt (may fail partway)
uv run scripts/translate_paper.py /path/to/full_text.md --resume

# Re-run: only translates failed chunks, skips cached ones
uv run scripts/translate_paper.py /path/to/full_text.md --resume
```

- Cache file: `.translate_cache.json` in the paper directory
- Auto-deleted on successful completion
- Invalidated if source file, backend, target language, or chunk size changes

## Error Handling

| Error | Action |
|-------|--------|
| API rate limit | Automatic retry with backoff |
| Token limit exceeded | Text automatically chunked; validation retries on structural mismatch |
| Network error | Retry up to 3 times |
| Partial failure with `--resume` | Successful chunks cached; re-run to resume |
