# Paper Ingestion & Analysis Toolkit

A high-performance toolkit for converting PDF research papers into AI-native Markdown formats. Designed for researchers who need to analyze papers with LLMs, preserving complex layouts, mathematical formulas, and extracting figures as standalone assets.

## Key Features

- **Multi-Engine Support**: GLM-OCR (cloud, default), [MinerU](https://github.com/opendatalab/MinerU) (GPU, highest quality), Docling (CPU fallback).
- **Smart PDF Routing**: GLM-OCR automatically selects the optimal upload strategy based on file size and source — small remote PDFs use direct URL upload; large or local PDFs use concurrent per-page image OCR.
- **Two-Scale Pipeline**: Decoupled rendering — lower resolution for API input (controls token cost), higher resolution for figure cropping (controls image quality), with the crop-scale rendering overlapping API wait time for zero additional latency.
- **10x MinerU Boost**: Persistent API Server mode eliminates model loading overhead (processing time ~130s → ~12s).
- **Asset Extraction**: Automatically extracts figures via layout-detected bounding boxes into `assets/` with padding compensation.
- **Math Support**: High-quality LaTeX formula recognition ($...$ and $$...$$), with automatic post-processing to fix common OCR-introduced formula errors (split words in `\text{}`, brace imbalance, broken delimiters, etc.).
- **Smart Organization**: Timestamped folders with auto-detected paper titles.
- **Modular Dependencies**: Engine dependencies are optional — install only what you need.

## Installation

Requires [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
cd paper-ingestion

# Base only (pypdf, pillow, requests)
uv sync

# Add MinerU engine (GPU-accelerated, from local mineru-fork)
uv sync --extra mineru

# Add Docling engine (CPU-friendly fallback)
uv sync --extra docling

# Install all engines
uv sync --all-extras
```

### Dependency Groups

| Group | Command | What's installed |
|-------|---------|------------------|
| Base | `uv sync` | `pypdf`, `pillow`, `requests` (~7 packages) |
| MinerU | `uv sync --extra mineru` | + `mineru[pipeline,api]` from local fork (~134 packages, includes torch + CUDA) |
| Docling | `uv sync --extra docling` | + `docling`, `torch` (~70 packages) |
| GLM-OCR | `uv sync` | Base only (uses `requests`), needs `GLM_API_ID` + `GLM_API_KEY` |
| All | `uv sync --all-extras` | Everything (~199 packages) |

> The `mineru` extra installs from the local `mineru-fork/` subtree as an editable dependency. Any changes to the fork code take effect immediately.

## Usage Guide

### 1. Default: GLM-OCR Engine (Cloud, No GPU)

Uses the Zhipu AI cloud API. No local GPU or heavy dependencies needed. Requires `GLM_API_ID` and `GLM_API_KEY` in environment or `paper-ingestion/.env`.

```bash
# Process a paper from URL (default engine: glm-ocr)
uv run scripts/ingest_paper.py "https://arxiv.org/pdf/2512.05905"

# Process a local file
uv run scripts/ingest_paper.py papers/my_paper.pdf

# Supply an established method name that differs from the formal title
uv run scripts/ingest_paper.py papers/my_paper.pdf --paper-name "LingBot-VA"
```

**How GLM-OCR routing works:**

| Source | File size | Strategy | Why |
|--------|-----------|----------|-----|
| Remote URL | ≤ 20 MB | URL direct upload | Fastest — single API request |
| Remote URL | > 20 MB | Per-page image OCR | URL upload times out on large PDFs |
| Local file | Any | Per-page image OCR | API rejects base64-encoded PDFs |

In per-page image mode, each PDF page is rendered to JPEG and uploaded as a base64 data URI. Pages are processed concurrently (default 10 workers) with automatic rate-limit retry and exponential backoff. Figure bounding boxes are cropped at a higher render scale (4x) while the API receives a lower scale (3x) to minimize token usage — the high-res rendering runs in parallel with API calls, adding no extra latency.

### 2. MinerU Engine (Highest Quality, GPU)

For the best quality, use MinerU with a persistent API server (10x speedup).

```bash
# Start server (requires --extra mineru)
CUDA_VISIBLE_DEVICES=0 uv run mineru-api --host 127.0.0.1 --port 8000

# Ingest (auto-detects server at 127.0.0.1:8000)
uv run scripts/ingest_paper.py paper.pdf --engine mineru
```

If the server is not running, it falls back to CLI mode (slower, ~1-2 mins per paper).

### 3. Docling Engine (No GPU)

```bash
# Requires: uv sync --extra docling
uv run scripts/ingest_paper.py paper.pdf --engine docling
```

## Output Structure

Papers are organized in timestamped folders:

```text
./20260202-My_Research_Paper_Title/
├── reference.pdf       # Original PDF
├── full_text-My_Research_Paper_Title.md # Converted Markdown (with YAML frontmatter)
├── notes.md            # Empty notes file for your analysis
└── assets/             # Extracted figures and images
    ├── image_001.webp
    └── image_002.webp
```

The original Markdown is named `full_text-{paper_name}.md`. By default the name
extracts a unique author-introduced method from the OCR abstract or introduction,
including an explicitly paired short alias. For example, a formal title without
`LingBot-VA` can still produce `full_text-LingBot-VA.md` from its abstract.
Missing or ambiguous evidence falls back to a short title prefix before a colon,
then the descriptive title. Use `--paper-name` for a verified name. Hyphens
and version numbers are preserved; spaces become underscores. The JSON output
includes `paper_name`, its source/evidence and the actual `markdown_path`. Existing papers are
not automatically renamed; `--force` refuses a competing original filename.

## Performance

**GLM-OCR (cloud):**

| Pages | Workers | Time | Notes |
|-------|---------|------|-------|
| 37 | 10 | ~41s | Optimal concurrency, zero rate limits |
| 37 | 1 | ~204s | Serial baseline |
| 5 | 8 | ~13s | Small PDFs via per-page image mode |

> Workers > 10 may trigger API rate limits (429) with diminishing returns.

**MinerU (GPU, H100):**

| Mode | Processing Time | Throughput | Notes |
|------|----------------|------------|-------|
| **API Server** | **~12s** | **~1.25 pg/s** | Recommended. Models loaded once. |
| CLI Mode | ~130s | ~0.11 pg/s | High overhead per run. |

## Configuration

Environment variables:

- `MINERU_API_HOST` / `MINERU_API_PORT`: API endpoint (default: `127.0.0.1:8000`)
- `MINERU_HYBRID_BATCH_RATIO`: Internal batch size (default: 16). Lower to 8 if OOM.
- `CUDA_VISIBLE_DEVICES`: GPU selection for server or CLI mode.
- `GLM_API_ID` / `GLM_API_KEY`: Credentials for GLM-OCR cloud engine (get from https://open.bigmodel.cn). Can also be set in `paper-ingestion/.env`.

---

**Developed for AI-Native Research Workflows.**
