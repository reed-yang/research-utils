---
name: paper-ingestion
description: Ingest PDF research papers and convert to Markdown for AI-native analysis. Use when user wants to read, analyze, or process a PDF paper, or provides a PDF URL/path. Uses GLM-OCR (cloud) by default, MinerU (GPU) or docling as alternatives.
---

# Paper Ingestion Tool

Convert PDF research papers to Markdown with image extraction, organized for AI-native analysis.

## Quick Reference

```bash
cd paper-ingestion

# From local file (default: glm-ocr cloud engine)
uv run scripts/ingest_paper.py /path/to/paper.pdf

# From URL
uv run scripts/ingest_paper.py "https://arxiv.org/pdf/2401.12345.pdf"

# MinerU engine (highest quality, requires GPU)
uv run scripts/ingest_paper.py paper.pdf --engine mineru

# Docling engine (fast, no GPU, lower quality)
uv run scripts/ingest_paper.py paper.pdf --engine docling

# Custom output directory
uv run scripts/ingest_paper.py paper.pdf --output-dir /path/to/readings

# Reuse a verified method name when it is not in the paper title
uv run scripts/ingest_paper.py paper.pdf --paper-name "LingBot-VA"
```

## MinerU API Server (Recommended)

For the best performance, run a persistent MinerU API server. This loads models once into GPU memory, giving ~10x speedup on subsequent papers.

```bash
cd paper-ingestion

# Start server (requires --extra mineru)
CUDA_VISIBLE_DEVICES=0 uv run mineru-api --host 127.0.0.1 --port 8000
```

The ingestion script auto-detects the server at `127.0.0.1:8000` and uses it when available. If the server is not running, it falls back to the `mineru` CLI (slower, loads models per-run).

## Engine Selection

| Scenario | Engine | Extra needed | Notes |
|----------|--------|--------------|-------|
| Default (cloud, no GPU) | `glm-ocr` | None (base only) | Cloud API, requires `GLM_API_KEY`, max 100 pages |
| Highest quality (GPU) | `mineru` | `--extra mineru` | GPU-accelerated, excellent math/tables |
| Fallback (fast, no GPU) | `docling` | `--extra docling` | Lower quality, good for quick previews |

## Output Structure

Files organized at `{cwd}/{YYYYMMDD}-{Sanitized_Title}/`:

```
20260916-Faster_WAM_Efficient_Future_Conditioning/
  reference.pdf    # Original PDF
  full_text-Faster-WAM.md # Original Markdown with YAML frontmatter
  notes.md         # Empty notes file
  assets/          # Extracted images
    image_001.webp
    image_002.webp
```

**Naming rules:**
- Timestamped prefix: `YYYYMMDD-`
- Title source: Use detected paper title after conversion (not URL string)
- Windows-safe: No `:?/\*<>|"` characters
- Duplicate check: Aborts if same title exists (ignoring date)
- Original body: `full_text-{paper_name}.md`; preserve method hyphens and version numbers, and replace spaces with underscores.
- Reuse a verified method name or title shorthand from the user's request or existing paper metadata with `--paper-name`. Do not invent an acronym from keywords or borrow a baseline's name.
- Without an explicit name, examine the OCR abstract, then introduction for a unique author-introduced method (for example, "we introduce LingBot-VA"). Prefer an explicitly paired short alias such as `Genie Envisioner Act 2.0 (GE-Act 2.0)`. Ignore quoted/code passages and related-work names. If the evidence is missing or ambiguous, use a short title prefix before a colon, then the bounded title. `LingBot-VA 2.0` becomes `LingBot-VA_2.0`.
- For less regular wording, the agent can identify the paper's own name semantically from an available official abstract or supplied metadata and pass it automatically with `--paper-name`. Verify it is this work's contribution, keep the exact spelling/version, and prefer the descriptive title when unsure. This does not require a separate naming-model API call.
- The JSON result's `markdown_path` is authoritative for subsequent summary/translation steps. `paper_name`, `paper_name_source` and `paper_name_evidence` are returned and stored in frontmatter. Keep the full formal title in `title`; there is no separate category/acronym generator in this script.
- Existing papers are not renamed. Even with `--force`, a different existing original filename must be resolved explicitly rather than leaving two competing originals. Lowercase two-letter suffixes such as `_ch` and `_zh` are reserved for translations.

## YAML Frontmatter

```yaml
---
title: "Paper Title"
paper_name: "Paper-Method"
paper_name_source: "author_introduction"
paper_name_evidence: "We introduce Paper-Method, a framework."
date_ingested: 2026-01-31
source_pdf: reference.pdf
conversion_engine: mineru
tags:
  - paper
aliases: []
---
```

## JSON Output

**Success:**
```json
{"status": "success", "markdown_path": ".../full_text-Faster-WAM.md", "title": "...", "paper_name": "Faster-WAM", "date": "2026-01-31", "paper_dir": "...", "engine_used": "mineru"}
```

**Error:**
```json
{"status": "error", "message": "...", "suggestion": "..."}
```

## Cloud Engine Configuration (GLM-OCR)

GLM-OCR uses the Zhipu AI cloud API. Set your API credentials:

```bash
# Option 1: Environment variables
export GLM_API_ID=your_id_here
export GLM_API_KEY=your_key_here

# Option 2: .env file
cp paper-ingestion/.env.template paper-ingestion/.env
# Edit .env and add your credentials
```

Get an API key at https://open.bigmodel.cn

If environment and `.env` values are absent, check the operator's existing
encrypted credential setup before reporting a missing key. Cortex installations
may use `~/.config/cortex/secrets.age` with `~/.config/cortex/age-key.txt`.
Decrypt in memory, parse only `GLM_API_ID` and `GLM_API_KEY` without evaluating
shell code, and pass them only in the ingestion child process environment.
Never print decrypted data, put credentials in argv, or write a plaintext `.env`
copy. An absent shell variable does not prove the encrypted store lacks it.
When working inside a configured Cortex checkout, prefer its shared launcher:
`.venv/bin/python -m tools.with_engine_secrets -- <skill-python> <ingest-script> <pdf>`.
It resolves declared engine references, including age, without manual exports.

**Limits**: PDF <= 50MB, max 100 pages per document.

## Error Handling

| Error | Action |
|-------|--------|
| Duplicate detected | Remove existing folder or use `--force` |
| MinerU timeout | Try `--engine docling` or `--engine glm-ocr` |
| Download failed | Check URL is accessible |
| GLM-OCR API key missing | Set `GLM_API_ID` and `GLM_API_KEY` in `.env` or environment |
| GLM-OCR URL timeout | Reuse the downloaded PDF as a local input to select the existing per-page GLM image mode; inspect page failures before accepting the result |
| GLM-OCR rate limited | Wait and retry (automatic), or reduce request frequency |

## Image Handling

- **All engines**: Extract images to `assets/` folder
- **Markdown references**: `![Fig1](./assets/image_001.webp)` (relative paths)
- **Syncthing compatible**: Small image files sync across devices

## Math Formatting

- Inline and display math are normalized to LaTeX using `$...$` / `$$...$$`
- After OCR conversion, a LaTeX formula repair pass (`fix_latex_formulas` from `agent-readings/scripts/`) automatically fixes common errors: split words in `\text{}` commands, brace imbalance (±1), spaced abbreviations, bare letter sequences, and confused pseudocode delimiters. This step is optional — if the repair module is not available, it is silently skipped.

## Environment Setup (if there is no env yet)

Dependencies are managed via `pyproject.toml` with optional extras. Install only what you need:

```bash
cd paper-ingestion

# Base only (pypdf, pillow, requests — enough for mineru API mode)
uv sync

# MinerU engine (local mineru-fork, GPU-accelerated)
uv sync --extra mineru

# Docling engine (CPU-friendly fallback)
uv sync --extra docling

# Everything
uv sync --all-extras
```

> **Note:** `mineru` is installed as an editable dependency from the local `mineru-fork/` subtree. Changes to the fork take effect immediately without reinstalling.

> **Next step:** Use the `summary` skill to generate a structured summary and extract keywords for the ingested paper.
