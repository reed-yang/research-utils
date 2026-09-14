#!/usr/bin/env -S uv run
"""
Paper Ingestion Tool - Convert PDF papers to Markdown for AI-native research workflow.

Multi-backend strategy:
  - glm-ocr (default): Cloud-based, no GPU needed, uses Zhipu AI API, requires API key
  - mineru: GPU-accelerated, local, highest quality for math/tables
  - docling: Fast, CPU/GPU, layout-aware, good for quick previews

Usage:
  uv run ingest_paper.py <pdf_path_or_url> [--engine mineru|docling|glm-ocr] [--output-dir <path>]
    [--image-format source|png|jpg|jpeg|webp] [--image-quality <1-100>] [--image-lossless] [--force]

Output:
  - Files organized in {cwd}/{YYYYMMDD}-{Sanitized_Title}/
  - Images saved to assets/ subfolder with relative paths in markdown
  - Single JSON object to stdout with status, paths, and metadata.
"""

import argparse
import csv
import io
import math
import os
import subprocess
import json
import re
import shutil
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, unquote


# ============================================================================
# Environment Loading
# ============================================================================

_SCRIPT_DIR = Path(__file__).resolve().parents[1]
_ROOT_ENV = _SCRIPT_DIR.parent / ".env"
_LOCAL_ENV = _SCRIPT_DIR / ".env"


def load_env_file(path: Path) -> None:
    """Load environment variables from a .env file."""
    if not path.exists():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key, value = key.strip(), value.strip()
            if value and (value[0] == value[-1]) and value[0] in ("'", '"'):
                value = value[1:-1]
            if key:
                os.environ.setdefault(key, value)
    except Exception:
        return


# Root .env takes priority (loaded first → setdefault locks the value),
# then local .env fills in any remaining keys.
load_env_file(_ROOT_ENV)
load_env_file(_LOCAL_ENV)


# ============================================================================
# Configuration
# ============================================================================


def get_output_root(output_dir: str | None = None) -> Path:
    """Get output root directory. Defaults to current working directory."""
    if output_dir:
        output_root = Path(output_dir).resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        return output_root
    return Path.cwd()


# ============================================================================
# Utility Functions
# ============================================================================


def sanitize_filename(name: str) -> str:
    r"""
    Convert filename to a Windows-safe folder name.
    Removes invalid chars: : ? / \ * < > | "
    Replaces spaces with underscores.
    """
    # Remove extension
    stem = Path(name).stem
    # Remove Windows-invalid characters: : ? / \ * < > | "
    sanitized = re.sub(r'[:\?/\\*<>|"]', "", stem)
    # Replace spaces and multiple hyphens with underscores
    sanitized = re.sub(r"[-\s]+", "_", sanitized)
    # Remove any other non-word characters except underscores
    sanitized = re.sub(r"[^\w_]", "", sanitized)
    # Remove leading/trailing underscores
    sanitized = sanitized.strip("_")
    # Collapse multiple underscores
    sanitized = re.sub(r"_+", "_", sanitized)
    return sanitized


def extract_title_from_markdown(markdown_content: str) -> str | None:
    """Extract title from markdown heading."""
    title_match = re.search(r"^\s*#{1,3}\s+(.+?)\s*$", markdown_content, re.MULTILINE)
    if title_match:
        return title_match.group(1).strip()
    return None


def extract_pdf_metadata_title(pdf_path: Path) -> str | None:
    """Extract title from PDF metadata if available."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(pdf_path))
        if reader.metadata and reader.metadata.title:
            return str(reader.metadata.title).strip()
    except Exception:
        return None
    return None


def resolve_paper_title(
    detected_title: str | None, markdown_content: str, pdf_path: Path
) -> str | None:
    """Resolve best available title for folder naming."""
    markdown_title = extract_title_from_markdown(markdown_content)
    if markdown_title and (
        not detected_title or looks_like_placeholder_title(detected_title)
    ):
        return markdown_title
    if detected_title:
        return detected_title
    metadata_title = extract_pdf_metadata_title(pdf_path)
    if metadata_title:
        return metadata_title
    return None


def looks_like_placeholder_title(title: str) -> bool:
    """Check if title is likely a filename or arXiv id."""
    normalized = title.strip()
    if not normalized:
        return True
    lowered = normalized.lower()
    if lowered.endswith(".pdf"):
        lowered = lowered[:-4]
    if "http" in lowered or "/" in lowered or "\\" in lowered:
        return True
    if re.fullmatch(r"\d{4}\.\d{4,5}(v\d+)?", lowered):
        return True
    if re.fullmatch(r"[0-9._-]+", lowered):
        return True
    return False


def apply_outside_code_blocks(text: str, transform) -> str:
    """Apply a transform to text outside fenced code blocks."""
    out = []
    buffer = []
    in_code_block = False
    for line in text.splitlines(keepends=True):
        if line.strip().startswith("```"):
            if not in_code_block:
                if buffer:
                    out.append(transform("".join(buffer)))
                    buffer = []
                in_code_block = True
            else:
                if buffer:
                    out.append("".join(buffer))
                    buffer = []
                in_code_block = False
            out.append(line)
        else:
            buffer.append(line)
    if buffer:
        if in_code_block:
            out.append("".join(buffer))
        else:
            out.append(transform("".join(buffer)))
    return "".join(out)


def normalize_math_delimiters(text: str) -> str:
    """Normalize math delimiters to $...$ and $$...$$ for Markdown."""
    text = re.sub(r"\\\((.+?)\\\)", r"$\1$", text, flags=re.DOTALL)
    text = re.sub(r"\\\[(.+?)\\\]", r"$$\1$$", text, flags=re.DOTALL)
    env_pattern = re.compile(
        r"\\begin\{(equation\*?|align\*?|multline\*?|gather\*?|split|cases)\}"
        r"(.*?)\\end\{\1\}",
        re.DOTALL,
    )

    def wrap_env(match: re.Match) -> str:
        start, end = match.span()
        before = text[max(0, start - 2) : start]
        after = text[end : end + 2]
        if before == "$$" and after == "$$":
            return match.group(0)
        return f"$$\n{match.group(0)}\n$$"

    return env_pattern.sub(wrap_env, text)


def replace_image_placeholders(
    markdown_content: str, image_count: int, image_ext: str = ".png"
) -> str:
    """Replace <!-- image --> placeholders with actual image references."""
    counter = [0]  # Use list to allow mutation in nested function

    def replacer(match):
        counter[0] += 1
        if counter[0] <= image_count:
            return f"![Figure {counter[0]}](./assets/image_{counter[0]:03d}{image_ext})"
        return match.group(0)

    return re.sub(r"<!--\s*image\s*-->", replacer, markdown_content)


def normalize_image_format(image_format: str | None) -> str:
    """Normalize image format selection."""
    if not image_format:
        return "source"
    normalized = image_format.strip().lower()
    if normalized in ("source", "auto", "original", "keep"):
        return "source"
    if normalized == "jpg":
        normalized = "jpeg"
    if normalized not in ("png", "jpeg", "webp"):
        raise ValueError(f"Unsupported image format: {image_format}")
    return normalized


def clamp_image_quality(quality: int) -> int:
    """Clamp image quality to 1-100."""
    return max(1, min(100, int(quality)))


def get_image_extension(
    image_format: str, source_ext: str | None = None, default_for_source: str = ".png"
) -> str:
    """Resolve output image extension."""
    if image_format == "source":
        if source_ext:
            source_ext = source_ext.lower()
            return source_ext if source_ext.startswith(".") else f".{source_ext}"
        return default_for_source
    if image_format == "jpeg":
        return ".jpg"
    return f".{image_format}"


def save_pil_image(
    pil_image,
    output_path: Path,
    image_format: str,
    image_quality: int,
    image_lossless: bool,
) -> None:
    """Save PIL image to target format."""
    fmt = "JPEG" if image_format == "jpeg" else image_format.upper()
    save_kwargs = {}

    if image_format == "jpeg":
        if pil_image.mode not in ("RGB", "L"):
            pil_image = pil_image.convert("RGB")
        save_kwargs.update(
            {"quality": image_quality, "optimize": True, "progressive": True}
        )
    elif image_format == "webp":
        save_kwargs.update({"method": 6, "lossless": image_lossless})
        save_kwargs["quality"] = 100 if image_lossless else image_quality
    elif image_format == "png":
        save_kwargs.update({"optimize": True})

    pil_image.save(str(output_path), format=fmt, **save_kwargs)


def save_image_bytes(
    img_bytes: bytes,
    output_path: Path,
    image_format: str,
    image_quality: int,
    image_lossless: bool,
) -> None:
    """Save image bytes in desired format."""
    if image_format == "source":
        with open(output_path, "wb") as f:
            f.write(img_bytes)
        return
    from PIL import Image

    with Image.open(io.BytesIO(img_bytes)) as img:
        save_pil_image(img, output_path, image_format, image_quality, image_lossless)


def reencode_image_file(
    src_path: Path,
    dest_path: Path,
    image_format: str,
    image_quality: int,
    image_lossless: bool,
) -> None:
    """Re-encode an on-disk image to the target format."""
    if image_format == "source":
        shutil.copy2(src_path, dest_path)
        return
    from PIL import Image

    with Image.open(src_path) as img:
        save_pil_image(img, dest_path, image_format, image_quality, image_lossless)


def wrap_inline_math(text: str) -> str:
    """
    Wrap common inline math patterns in $...$ delimiters.
    Applied outside code blocks only.
    """

    def transform(segment: str) -> str:
        # Variables with subscripts: x_i, P_ref, z_0, etc.
        segment = re.sub(r"\b([A-Za-z])\s*([_])\s*(\w+)\b", r"$\1_\3$", segment)

        # Greek letters as standalone
        greek = r"[αβγδεζηθικλμνξοπρστυφχψωΑΒΓΔΘΛΞΠΣΦΨΩ∈∀∃∅∇∞∝∑∏∫∂]"
        segment = re.sub(rf"(?<![\$A-Za-z])({greek})(?![A-Za-z])", r"$\1$", segment)

        # Comparison operators with numbers: 1 ≤ j ≤ N
        segment = re.sub(r"(\d+)\s*([≤≥≈≠<>])\s*(\w+)", r"$\1 \2 \3$", segment)
        segment = re.sub(r"(\w+)\s*([≤≥≈≠])\s*(\d+)", r"$\1 \2 \3$", segment)

        return segment

    return apply_outside_code_blocks(text, transform)


def output_json(data: dict) -> None:
    """Print JSON to stdout (agent interface)."""
    print(json.dumps(data, ensure_ascii=False))


def output_error(message: str, suggestion: str = None) -> None:
    """Output error JSON and exit."""
    error_data = {"status": "error", "message": message}
    if suggestion:
        error_data["suggestion"] = suggestion
    output_json(error_data)
    sys.exit(1)


def is_url(path: str) -> bool:
    """Check if the input is a URL."""
    parsed = urlparse(path)
    return parsed.scheme in ("http", "https")


def normalize_arxiv_url(url: str) -> str:
    """Convert arxiv abstract URL to PDF URL.

    https://arxiv.org/abs/2401.12345 -> https://arxiv.org/pdf/2401.12345
    https://arxiv.org/abs/2401.12345v2 -> https://arxiv.org/pdf/2401.12345v2
    """
    import re
    return re.sub(r"arxiv\.org/abs/", "arxiv.org/pdf/", url)


def download_pdf(url: str) -> Path:
    """Download PDF from URL to a temporary file."""
    import requests

    try:
        response = requests.get(url, timeout=60, stream=True)
        response.raise_for_status()

        # Try to get filename from Content-Disposition or URL
        filename = None
        if "Content-Disposition" in response.headers:
            cd = response.headers["Content-Disposition"]
            if "filename=" in cd:
                filename = cd.split("filename=")[-1].strip("\"'")

        if not filename:
            # Extract from URL path
            url_path = urlparse(url).path
            filename = unquote(Path(url_path).name)

        if not filename or not filename.lower().endswith(".pdf"):
            filename = "downloaded_paper.pdf"

        # Save to temp file
        temp_dir = Path(tempfile.mkdtemp())
        temp_path = temp_dir / filename

        with open(temp_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        return temp_path

    except requests.RequestException as e:
        output_error(f"Failed to download PDF: {e}")


def check_duplicate(sanitized_title: str, output_root: Path) -> bool:
    """
    Check if a folder with the same title already exists (ignoring date prefix).
    Returns True if duplicate found.
    """
    # Pattern: {YYYYMMDD}-{title} or just {title}
    for item in output_root.iterdir():
        if item.is_dir():
            dir_name = item.name
            # Remove date prefix if present (format: YYYYMMDD-)
            if re.match(r"^\d{8}-", dir_name):
                existing_title = dir_name[9:]  # Skip "YYYYMMDD-"
            else:
                existing_title = dir_name

            if existing_title == sanitized_title:
                return True
    return False


# ============================================================================
# Docling Backend (with Image Extraction)
# ============================================================================


def get_all_free_gpus(
    max_memory_mb: int = 2000, max_util_percent: int = 10
) -> list[str]:
    """
    Get indices of all free GPUs, sorted by memory usage (lowest first).

    Priority:
    1. If CUDA_VISIBLE_DEVICES is set, use those GPU indices directly
    2. Otherwise, auto-detect free GPUs via nvidia-smi

    Returns empty list if no GPU is free or nvidia-smi fails.
    """
    # Priority 1: Respect CUDA_VISIBLE_DEVICES if already set by user/container
    cuda_visible = os.environ.get("CUDA_VISIBLE_DEVICES")
    if cuda_visible is not None and cuda_visible.strip():
        # User explicitly set GPU indices - use them as-is
        # These are already the logical indices visible to this process
        indices = [idx.strip() for idx in cuda_visible.split(",") if idx.strip()]
        print(f"Using CUDA_VISIBLE_DEVICES: {indices}", file=sys.stderr)
        return indices

    # Priority 2: Auto-detect free GPUs via nvidia-smi
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,memory.used,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return []

        reader = csv.reader(io.StringIO(result.stdout.strip()))
        free_gpus = []
        for row in reader:
            try:
                idx, mem, util = map(int, row)
                if mem < max_memory_mb and util < max_util_percent:
                    free_gpus.append((idx, mem))
            except ValueError:
                continue

        # Sort by memory usage (ascending)
        free_gpus.sort(key=lambda x: x[1])
        return [str(gpu[0]) for gpu in free_gpus]

    except FileNotFoundError:
        return []  # nvidia-smi not found
    except Exception:
        return []


def get_pdf_page_count(pdf_path: Path) -> int:
    """Get total page count of a PDF using pypdf."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(pdf_path))
        return len(reader.pages)
    except Exception:
        return 0


def split_pdf_to_chunks(
    pdf_path: Path, num_chunks: int, tmpdir: Path
) -> list[tuple[Path, int, int]]:
    """
    Split PDF into chunks for parallel processing.

    Returns list of (chunk_pdf_path, start_page, end_page) tuples.
    Pages are 0-indexed.
    """
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(str(pdf_path))
    total_pages = len(reader.pages)

    if total_pages == 0:
        return []

    # Limit chunks to page count
    num_chunks = min(num_chunks, total_pages)
    pages_per_chunk = math.ceil(total_pages / num_chunks)

    chunks = []
    for i in range(num_chunks):
        start_page = i * pages_per_chunk
        end_page = min((i + 1) * pages_per_chunk - 1, total_pages - 1)

        if start_page > end_page:
            break

        # Create chunk PDF
        writer = PdfWriter()
        for page_num in range(start_page, end_page + 1):
            writer.add_page(reader.pages[page_num])

        chunk_path = tmpdir / f"chunk_{i:02d}.pdf"
        with open(chunk_path, "wb") as f:
            writer.write(f)

        chunks.append((chunk_path, start_page, end_page))

    return chunks


def run_mineru_on_chunk(
    chunk_pdf: Path,
    gpu_idx: str,
    output_dir: Path,
    chunk_idx: int,
) -> tuple[int, str, Path | None]:
    """
    Run MinerU on a single PDF chunk using specified GPU.

    Returns (chunk_idx, markdown_content, images_dir or None).
    """
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = gpu_idx
    env["MINERU_HYBRID_BATCH_RATIO"] = "8"

    try:
        result = subprocess.run(
            [
                "mineru",
                "-p",
                str(chunk_pdf),
                "-o",
                str(output_dir),
                "-b",
                "hybrid-auto-engine",
                "-l",
                "en",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=300,  # 5 min timeout per chunk
        )

        if result.returncode != 0:
            return (chunk_idx, "", None)

        # Find generated markdown
        md_files = list(output_dir.rglob("*.md"))
        if not md_files:
            return (chunk_idx, "", None)

        md_file = md_files[0]
        markdown_content = md_file.read_text(encoding="utf-8")

        # Find images directory
        images_dir = md_file.parent / "images"
        if not images_dir.exists():
            images_dir = None

        return (chunk_idx, markdown_content, images_dir)

    except Exception:
        return (chunk_idx, "", None)


def merge_chunk_results(
    chunk_results: list[tuple[int, str, Path | None]],
    assets_dir: Path,
    image_format: str,
    image_quality: int,
    image_lossless: bool,
) -> tuple[str, int]:
    """
    Merge markdown from multiple chunks and renumber images sequentially.

    Returns (merged_markdown, total_image_count).
    """
    # Sort by chunk index
    chunk_results.sort(key=lambda x: x[0])

    image_format = normalize_image_format(image_format)
    image_quality = clamp_image_quality(image_quality)

    assets_dir.mkdir(parents=True, exist_ok=True)
    merged_parts = []
    global_image_counter = 0

    for chunk_idx, markdown_content, images_dir in chunk_results:
        if not markdown_content:
            continue

        # Build image mapping for this chunk
        image_map = {}  # old_ref -> new_ref

        if images_dir and images_dir.exists():
            for img_file in sorted(images_dir.iterdir()):
                if img_file.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                    global_image_counter += 1
                    source_ext = img_file.suffix.lower()
                    target_ext = get_image_extension(
                        image_format, source_ext, default_for_source=".png"
                    )
                    new_name = f"image_{global_image_counter:03d}{target_ext}"
                    dest_path = assets_dir / new_name
                    reencode_image_file(
                        img_file,
                        dest_path,
                        image_format,
                        image_quality,
                        image_lossless,
                    )

                    # Map various possible references
                    old_ref = f"images/{img_file.name}"
                    image_map[old_ref] = f"./assets/{new_name}"
                    image_map[img_file.name] = f"./assets/{new_name}"

        # Rewrite image paths in this chunk's markdown
        def replace_img_path(match):
            alt_text = match.group(1)
            old_path = match.group(2)

            if old_path in image_map:
                return f"![{alt_text}]({image_map[old_path]})"

            # Try filename only
            filename = Path(old_path).name
            if filename in image_map:
                return f"![{alt_text}]({image_map[filename]})"

            return match.group(0)

        updated_content = re.sub(
            r"!\[([^\]]*)\]\(([^)]+)\)",
            replace_img_path,
            markdown_content,
        )

        merged_parts.append(updated_content)

    # Join with page breaks
    merged_markdown = "\n\n---\n\n".join(merged_parts)

    return merged_markdown, global_image_counter


def convert_with_docling(
    pdf_path: Path,
    assets_dir: Path,
    images_scale: float,
    image_format: str,
    image_quality: int,
    image_lossless: bool,
) -> tuple[str, str | None]:
    """
    Convert PDF to Markdown using IBM Docling with image extraction.
    Returns (markdown_content, detected_title).
    """
    try:
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.datamodel.base_models import InputFormat

        image_format = normalize_image_format(image_format)
        image_quality = clamp_image_quality(image_quality)
        output_format = "png" if image_format == "source" else image_format
        image_ext = get_image_extension(output_format)

        # Configure pipeline with image extraction
        pipeline_options = PdfPipelineOptions()
        pipeline_options.generate_picture_images = True
        pipeline_options.generate_table_images = True
        pipeline_options.do_formula_enrichment = True
        # Higher value => higher resolution (1.0 ~= 72 DPI)
        pipeline_options.images_scale = images_scale

        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )

        result = converter.convert(str(pdf_path))
        doc = result.document

        # Extract title from document metadata
        detected_title = None
        if hasattr(doc, "name") and doc.name:
            detected_title = doc.name

        # Export markdown
        markdown_content = doc.export_to_markdown(image_mode="referenced")

        # Save images to assets directory and rewrite paths
        assets_dir.mkdir(parents=True, exist_ok=True)
        image_counter = 0

        if hasattr(doc, "pictures") and doc.pictures:
            for pic in doc.pictures:
                if hasattr(pic, "image") and pic.image:
                    image_counter += 1
                    image_name = f"image_{image_counter:03d}{image_ext}"
                    image_path = assets_dir / image_name
                    save_pil_image(
                        pic.image.pil_image,
                        image_path,
                        output_format,
                        image_quality,
                        image_lossless,
                    )

        # Replace <!-- image --> placeholders with actual image references
        markdown_content = replace_image_placeholders(
            markdown_content, image_counter, image_ext
        )

        return markdown_content, detected_title

    except ImportError as e:
        output_error(
            f"Docling import failed: {e}",
            "Ensure docling is installed: uv pip install docling",
        )
    except Exception as e:
        output_error(f"Docling conversion failed: {e}")


# ============================================================================
# MinerU Backend (High-Quality GPU-Accelerated)
# ============================================================================

# Default mineru-api server configuration
MINERU_API_HOST = os.environ.get("MINERU_API_HOST", "127.0.0.1")
MINERU_API_PORT = int(os.environ.get("MINERU_API_PORT", "8000"))


def check_mineru_api_server(host: str = None, port: int = None) -> bool:
    """
    Check if mineru-api server is running and healthy.

    Returns True if server is available.
    """
    import requests

    host = host or MINERU_API_HOST
    port = port or MINERU_API_PORT

    try:
        response = requests.get(f"http://{host}:{port}/docs", timeout=2)
        return response.status_code == 200
    except Exception:
        return False


def convert_via_mineru_api(
    pdf_path: Path,
    assets_dir: Path,
    host: str = None,
    port: int = None,
    image_format: str = "source",
    image_quality: int = 95,
    image_lossless: bool = False,
) -> tuple[str, str | None]:
    """
    Convert PDF via mineru-api REST server (persistent model, no reload overhead).

    Requires mineru-api server to be running:
        CUDA_VISIBLE_DEVICES=0 mineru-api --host 127.0.0.1 --port 8000

    Returns (markdown_content, detected_title).
    """
    import requests

    host = host or MINERU_API_HOST
    port = port or MINERU_API_PORT
    image_format = normalize_image_format(image_format)
    image_quality = clamp_image_quality(image_quality)

    try:
        # Upload PDF and convert via API
        # Endpoint is /file_parse per MinerU's fast_api.py
        with open(pdf_path, "rb") as f:
            response = requests.post(
                f"http://{host}:{port}/file_parse",
                files={"files": (pdf_path.name, f, "application/pdf")},
                data={
                    "backend": "hybrid-auto-engine",
                    "parse_method": "auto",
                    "lang_list": "en",
                    "return_md": "true",
                    "return_images": "true",
                    "formula_enable": "true",
                    "table_enable": "true",
                },
                timeout=600,  # 10 min timeout for large PDFs
            )

        if response.status_code != 200:
            raise RuntimeError(
                f"API error: {response.status_code} - {response.text[:200]}"
            )

        result = response.json()

        # API response format:
        # {"backend": "...", "version": "...", "results": {
        #   "pdf_stem": {"md_content": "...", "images": {"img.jpg": "data:image/jpeg;base64,..."}}
        # }}
        results = result.get("results", {})
        if not results:
            raise RuntimeError("API returned empty results")

        # Get first (only) result since we send one file
        pdf_stem = pdf_path.stem
        pdf_result = results.get(pdf_stem, {})
        if not pdf_result:
            # Try to get first available result
            pdf_result = next(iter(results.values()), {})

        # Extract markdown content
        markdown_content = pdf_result.get("md_content", "")
        if not markdown_content:
            raise RuntimeError("API returned no markdown content")

        # Handle images from API response
        assets_dir.mkdir(parents=True, exist_ok=True)
        image_counter = 0
        image_map = {}

        # API returns images as base64 data URIs: {"img.jpg": "data:image/jpeg;base64,..."}
        images_dict = pdf_result.get("images", {})
        for img_filename, img_data_uri in images_dict.items():
            image_counter += 1
            # Determine extension from filename or default to jpg
            source_ext = Path(img_filename).suffix or ".jpg"
            target_ext = get_image_extension(
                image_format, source_ext, default_for_source=".jpg"
            )
            img_name = f"image_{image_counter:03d}{target_ext}"
            img_path = assets_dir / img_name

            # Parse data URI: data:image/jpeg;base64,/9j/4AAQ...
            if isinstance(img_data_uri, str) and ";base64," in img_data_uri:
                import base64

                base64_data = img_data_uri.split(";base64,", 1)[1]
                img_bytes = base64.b64decode(base64_data)
                save_image_bytes(
                    img_bytes,
                    img_path,
                    image_format,
                    image_quality,
                    image_lossless,
                )
                # Map original reference to new path
                image_map[f"images/{img_filename}"] = f"./assets/{img_name}"
                image_map[img_filename] = f"./assets/{img_name}"

        # Rewrite image paths in markdown
        def replace_img_path(match):
            alt_text = match.group(1)
            old_path = match.group(2)
            if old_path in image_map:
                return f"![{alt_text}]({image_map[old_path]})"
            for old_ref, new_ref in image_map.items():
                if old_ref in old_path or old_path in old_ref:
                    return f"![{alt_text}]({new_ref})"
            return match.group(0)

        markdown_content = re.sub(
            r"!\[([^\]]*)\]\(([^)]+)\)",
            replace_img_path,
            markdown_content,
        )

        detected_title = extract_title_from_markdown(markdown_content)
        return markdown_content, detected_title

    except Exception as e:
        raise RuntimeError(f"mineru-api conversion failed: {e}")


# Global to track conversion metadata for logging
_conversion_metadata = {}


def get_conversion_metadata() -> dict:
    """Get metadata about the last conversion for logging."""
    return _conversion_metadata.copy()


def convert_with_mineru(
    pdf_path: Path,
    assets_dir: Path,
    image_format: str,
    image_quality: int,
    image_lossless: bool,
) -> tuple[str, str | None]:
    """
    Convert PDF to Markdown using MinerU (hybrid-auto-engine for best accuracy).

    Strategy:
    1. Try mineru-api server first (persistent model, no reload overhead)
    2. Fall back to subprocess CLI if server not available

    Automatically detects all free GPUs and uses parallel page-chunking
    when multiple GPUs are available.

    Returns (markdown_content, detected_title).
    """
    global _conversion_metadata
    image_format = normalize_image_format(image_format)
    image_quality = clamp_image_quality(image_quality)
    page_count = get_pdf_page_count(pdf_path)
    # === PRIORITY 1: Try mineru-api server (persistent model) ===
    if check_mineru_api_server():
        print(
            f"MinerU: Using API server at {MINERU_API_HOST}:{MINERU_API_PORT} (persistent model)",
            file=sys.stderr,
        )
        try:
            _conversion_metadata = {
                "backend": "mineru",
                "mode": "api",
                "api_host": MINERU_API_HOST,
                "api_port": MINERU_API_PORT,
                "page_count": page_count,
                "image_format": image_format,
                "image_quality": image_quality,
                "image_lossless": image_lossless,
            }
            return convert_via_mineru_api(
                pdf_path,
                assets_dir,
                image_format=image_format,
                image_quality=image_quality,
                image_lossless=image_lossless,
            )
        except Exception as e:
            print(f"MinerU API failed: {e}, falling back to CLI", file=sys.stderr)

    # === PRIORITY 2: Fall back to subprocess CLI ===
    MAX_WORKERS = 4  # Cap parallel workers to avoid OOM

    # Detect all free GPUs
    free_gpus = get_all_free_gpus()
    page_count = get_pdf_page_count(pdf_path)

    if not free_gpus:
        print("MinerU: No free GPU found, using default/CPU", file=sys.stderr)
        free_gpus = [""]  # Empty string = use default CUDA device

    # Determine optimal worker count
    num_workers = min(len(free_gpus), page_count, MAX_WORKERS)
    num_workers = max(1, num_workers)  # At least 1 worker

    # Use selected GPUs (take first N)
    selected_gpus = free_gpus[:num_workers]

    # Report configuration
    batch_ratio = 8
    if num_workers > 1:
        print(
            f"MinerU using {num_workers} GPUs: {selected_gpus} "
            f"({num_workers} workers, batch_ratio={batch_ratio})",
            file=sys.stderr,
        )
    else:
        gpu_str = selected_gpus[0] if selected_gpus[0] else "default"
        print(
            f"MinerU using GPU: {gpu_str} (batch_ratio={batch_ratio})", file=sys.stderr
        )

    # Store metadata for logging
    _conversion_metadata = {
        "backend": "mineru",
        "mode": "cli",
        "gpus": ",".join(selected_gpus) if selected_gpus[0] else "default",
        "num_workers": num_workers,
        "batch_ratio": batch_ratio,
        "page_count": page_count,
        "image_format": image_format,
        "image_quality": image_quality,
        "image_lossless": image_lossless,
    }

    # Create temporary directory
    tmpdir = Path(tempfile.mkdtemp())

    try:
        # === SINGLE WORKER PATH ===
        if num_workers == 1:
            env = os.environ.copy()
            env["MINERU_HYBRID_BATCH_RATIO"] = "8"
            if selected_gpus[0]:
                env["CUDA_VISIBLE_DEVICES"] = selected_gpus[0]

            result = subprocess.run(
                [
                    "mineru",
                    "-p",
                    str(pdf_path),
                    "-o",
                    str(tmpdir),
                    "-b",
                    "hybrid-auto-engine",
                    "-l",
                    "en",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=600,
            )

            if result.returncode != 0:
                output_error(
                    f"MinerU conversion failed: {result.stderr[:500]}",
                    "Check MinerU installation: uv pip install -U 'mineru[all]'",
                )

            # Find generated markdown
            md_files = list(tmpdir.rglob("*.md"))
            if not md_files:
                output_error("MinerU produced no markdown output")

            pdf_stem = pdf_path.stem
            md_file = next((f for f in md_files if f.stem == pdf_stem), md_files[0])
            markdown_content = md_file.read_text(encoding="utf-8")

            # Copy images to assets directory
            assets_dir.mkdir(parents=True, exist_ok=True)
            image_map = {}
            image_counter = 0

            images_dir = md_file.parent / "images"
            if images_dir.exists():
                for img_file in sorted(images_dir.iterdir()):
                    if img_file.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                        image_counter += 1
                        source_ext = img_file.suffix.lower()
                        target_ext = get_image_extension(
                            image_format, source_ext, default_for_source=".png"
                        )
                        new_name = f"image_{image_counter:03d}{target_ext}"
                        dest_path = assets_dir / new_name
                        reencode_image_file(
                            img_file,
                            dest_path,
                            image_format,
                            image_quality,
                            image_lossless,
                        )
                        old_ref = f"images/{img_file.name}"
                        image_map[old_ref] = f"./assets/{new_name}"

            # Rewrite image paths
            def replace_img_path(match):
                alt_text = match.group(1)
                old_path = match.group(2)
                if old_path in image_map:
                    return f"![{alt_text}]({image_map[old_path]})"
                for old_ref, new_ref in image_map.items():
                    if old_ref.endswith(Path(old_path).name):
                        return f"![{alt_text}]({new_ref})"
                return match.group(0)

            markdown_content = re.sub(
                r"!\[([^\]]*)\]\(([^)]+)\)",
                replace_img_path,
                markdown_content,
            )

            detected_title = extract_title_from_markdown(markdown_content)
            return markdown_content, detected_title

        # === MULTI-WORKER PARALLEL PATH ===
        chunks_dir = tmpdir / "chunks"
        chunks_dir.mkdir(exist_ok=True)

        # Split PDF into chunks
        chunks = split_pdf_to_chunks(pdf_path, num_workers, chunks_dir)

        if not chunks:
            output_error("Failed to split PDF into chunks")

        print(
            f"  Split into {len(chunks)} chunks for parallel processing",
            file=sys.stderr,
        )

        # Process chunks in parallel
        chunk_results = []

        def process_chunk(args):
            chunk_idx, chunk_info, gpu_idx = args
            chunk_pdf, start_pg, end_pg = chunk_info
            output_dir = tmpdir / f"output_{chunk_idx:02d}"
            output_dir.mkdir(exist_ok=True)
            return run_mineru_on_chunk(chunk_pdf, gpu_idx, output_dir, chunk_idx)

        # Prepare work items (round-robin GPU assignment if more chunks than GPUs)
        work_items = []
        for i, chunk in enumerate(chunks):
            gpu_idx = selected_gpus[i % len(selected_gpus)]
            work_items.append((i, chunk, gpu_idx))

        # Run parallel
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(process_chunk, item) for item in work_items]
            for future in as_completed(futures):
                result = future.result()
                chunk_results.append(result)

        # Merge results
        markdown_content, total_images = merge_chunk_results(
            chunk_results,
            assets_dir,
            image_format,
            image_quality,
            image_lossless,
        )

        if not markdown_content:
            output_error("All MinerU chunk processes failed")

        print(f"  Merged {len(chunks)} chunks, {total_images} images", file=sys.stderr)

        detected_title = extract_title_from_markdown(markdown_content)
        return markdown_content, detected_title

    except subprocess.TimeoutExpired:
        output_error(
            "MinerU conversion timed out (>10 minutes)",
            "PDF may be too large. Try --engine docling instead.",
        )
    except FileNotFoundError:
        output_error(
            "MinerU command not found",
            "Install MinerU: uv pip install -U 'mineru[all]'",
        )
    except Exception as e:
        output_error(f"MinerU conversion failed: {e}")


# ============================================================================
# GLM-OCR Backend (Cloud API)
# ============================================================================


def _glm_ocr_single_page_image(
    pil_image,  # PIL Image
    auth_token: str,
    page_num: int,
    total_pages: int,
    debug: bool = False,
) -> dict:
    """OCR a single page image via GLM-OCR API. Returns the full API response dict."""
    import base64
    import requests

    # Encode PIL image as JPEG Q90 base64
    buf = io.BytesIO()
    pil_image.save(buf, format="JPEG", quality=90)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    file_value = f"data:image/jpeg;base64,{b64}"

    payload_kb = len(b64) * 3 // 4 // 1024
    print(
        f"GLM-OCR: Page {page_num + 1}/{total_pages} (image mode, ~{payload_kb} KB)",
        file=sys.stderr,
    )

    url = "https://open.bigmodel.cn/api/paas/v4/layout_parsing"
    headers = {
        "Authorization": auth_token,
        "Content-Type": "application/json",
    }
    payload = {"model": "glm-ocr", "file": file_value}

    max_retries = 3
    max_rate_limit_retries = 6  # 429s get more patience (backoff: 2,4,8,16,32,64s)
    last_error = None
    error_attempts = 0
    rate_limit_attempts = 0
    while error_attempts < max_retries and rate_limit_attempts < max_rate_limit_retries:
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=120)

            if response.status_code == 429:
                rate_limit_attempts += 1
                wait_time = 2 ** rate_limit_attempts
                print(
                    f"GLM-OCR: Rate limited on page {page_num + 1}, waiting {wait_time}s "
                    f"({rate_limit_attempts}/{max_rate_limit_retries})",
                    file=sys.stderr,
                )
                time.sleep(wait_time)
                continue

            if response.status_code != 200:
                error_attempts += 1
                last_error = f"GLM-OCR API error on page {page_num + 1}: {response.status_code} - {response.text[:300]}"
                if error_attempts < max_retries:
                    time.sleep(2 ** error_attempts)
                    continue
                output_error(last_error)

            return response.json()

        except requests.Timeout:
            error_attempts += 1
            last_error = f"GLM-OCR: Page {page_num + 1} timed out (>120s)"
            if error_attempts < max_retries:
                time.sleep(2 ** error_attempts)
                continue
            output_error(last_error, "The page image may be too complex.")
        except requests.RequestException as e:
            error_attempts += 1
            last_error = f"GLM-OCR: Page {page_num + 1} request failed: {e}"
            if error_attempts < max_retries:
                time.sleep(2 ** error_attempts)
                continue
            output_error(last_error)

    output_error(last_error or f"GLM-OCR: Page {page_num + 1} failed after all retries")
    return {}  # unreachable (output_error exits)


def _glm_ocr_via_page_images(
    pdf_path: Path,
    auth_token: str,
    page_count: int,
    render_scale: float = 3.0,
    crop_scale: float = 4.0,
    max_workers: int = 10,
    debug: bool = False,
) -> tuple[str, list, dict, dict, dict]:
    """
    OCR a PDF by rendering each page as an image and sending concurrently.

    Two-scale pipeline:
    - render_scale (3x): controls API input size and token cost
    - crop_scale (4x): controls bbox crop quality, rendered during API wait

    Returns (merged_markdown, merged_layout_details, merged_data_info,
             total_usage, crop_images_dict).
    crop_images_dict maps page_num -> PIL Image at crop_scale for bbox reuse.
    """
    import pypdfium2 as pdfium

    pdf_doc = pdfium.PdfDocument(str(pdf_path))
    actual_pages = min(page_count, len(pdf_doc))
    results_by_page = {}  # pg_num -> API result dict

    print(
        f"GLM-OCR: Rendering + sending {actual_pages} pages with {max_workers} concurrent workers",
        file=sys.stderr,
    )

    def _ocr_page(pil_img, pg_num):
        return pg_num, _glm_ocr_single_page_image(
            pil_img, auth_token, pg_num, actual_pages, debug,
        )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Phase 1: render @render_scale and submit OCR immediately
        futures = {}
        for pg_num in range(actual_pages):
            pg = pdf_doc[pg_num]
            bitmap = pg.render(scale=render_scale)
            pil_img = bitmap.to_pil()
            futures[executor.submit(_ocr_page, pil_img, pg_num)] = pg_num

        # Phase 2: while API calls are in flight, render @crop_scale for bbox crops
        # This overlaps CPU rendering with network IO — zero additional wait
        crop_images = {}
        if crop_scale != render_scale:
            print(
                f"GLM-OCR: Rendering {actual_pages} pages @{crop_scale}x for image extraction (during API wait)",
                file=sys.stderr,
            )
            for pg_num in range(actual_pages):
                pg = pdf_doc[pg_num]
                bitmap = pg.render(scale=crop_scale)
                crop_images[pg_num] = bitmap.to_pil()
        else:
            # Same scale: reuse is handled by caller checking None
            pass

        # Phase 3: collect API results
        for future in as_completed(futures):
            pg_num, result = future.result()
            results_by_page[pg_num] = result

    pdf_doc.close()

    # Merge results in page order
    all_md = []
    all_layout_details = []
    all_pages_info = []
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    for pg_num in range(actual_pages):
        result = results_by_page[pg_num]

        # Merge markdown with page=0 rewritten to correct global page number
        md = result.get("md_results", "")
        if pg_num > 0:
            md = re.sub(r"page=0,bbox=", f"page={pg_num},bbox=", md)
        all_md.append(md)

        # Merge layout_details: API returns [[{...}, ...]] (list of lists, one per page)
        page_details = result.get("layout_details", [])
        if page_details and isinstance(page_details[0], list):
            page_details = page_details[0]  # single-page response, unwrap
        for detail in page_details:
            if isinstance(detail, dict):
                detail["page"] = pg_num
            all_layout_details.append(detail)

        # Collect per-page data_info
        pages_info = result.get("data_info", {}).get("pages", [])
        if pages_info:
            page_info = pages_info[0]
            page_info["page_number"] = pg_num
            all_pages_info.append(page_info)

        # Accumulate usage
        usage = result.get("usage", {})
        for k in total_usage:
            total_usage[k] += usage.get(k, 0)

    merged_data_info = {"pages": all_pages_info}
    return "\n\n".join(all_md), all_layout_details, merged_data_info, total_usage, crop_images or None


def convert_with_glm_ocr(
    pdf_path: Path,
    assets_dir: Path,
    image_format: str,
    image_quality: int,
    image_lossless: bool,
    debug: bool = False,
    source_url: str | None = None,
) -> tuple[str, str | None]:
    """
    Convert PDF to Markdown using GLM-OCR cloud API (Zhipu AI layout parsing).

    Smart routing based on file size and source:
    - Small remote PDF (<=20MB): URL direct upload (single request, fastest)
    - Large remote PDF (>20MB): per-page image base64 (avoids URL upload timeout)
    - Local file: per-page image base64 (base64 PDF is rejected by API)

    Requires GLM_API_ID and GLM_API_KEY environment variables or paper-ingestion/.env file.

    Limits: PDF <= 50MB, Images <= 10MB, max 100 pages.

    Returns (markdown_content, detected_title).
    """
    import base64
    import requests

    global _conversion_metadata

    # --- Validate API credentials ---
    api_id = os.environ.get("GLM_API_ID", "").strip()
    api_key = os.environ.get("GLM_API_KEY", "").strip()
    if not api_key:
        output_error(
            "GLM_API_KEY is not set",
            "Create paper-ingestion/.env with GLM_API_ID and GLM_API_KEY, "
            "or set them as environment variables. "
            "Get your key at https://open.bigmodel.cn",
        )

    # Build authorization token: id.key format
    auth_token = f"{api_id}.{api_key}" if api_id else api_key

    # --- Validate file limits ---
    file_size = pdf_path.stat().st_size
    if file_size > 50 * 1024 * 1024:
        output_error(
            f"PDF too large for GLM-OCR: {file_size / (1024 * 1024):.1f}MB (limit: 50MB)",
            "Try --engine mineru or --engine docling for large PDFs.",
        )

    page_count = get_pdf_page_count(pdf_path)
    if page_count > 100:
        output_error(
            f"PDF has {page_count} pages (GLM-OCR limit: 100 pages)",
            "Try --engine mineru or --engine docling for large PDFs.",
        )

    # --- Store conversion metadata ---
    image_format = normalize_image_format(image_format)
    image_quality = clamp_image_quality(image_quality)
    _conversion_metadata = {
        "backend": "glm-ocr",
        "mode": "cloud",
        "page_count": page_count,
        "file_size_mb": round(file_size / (1024 * 1024), 2),
        "image_format": image_format,
        "image_quality": image_quality,
        "image_lossless": image_lossless,
    }

    # --- Smart routing: choose URL vs per-page image mode ---
    SIZE_THRESHOLD_MB = 20  # URL mode works reliably below this

    use_image_mode = False
    if source_url and file_size <= SIZE_THRESHOLD_MB * 1024 * 1024:
        # Small remote PDF: URL direct (fastest, single request)
        file_value = source_url
        _conversion_metadata["mode"] = "url"
        print(
            f"GLM-OCR: Using source URL directly ({file_size / (1024*1024):.1f}MB <= {SIZE_THRESHOLD_MB}MB threshold)",
            file=sys.stderr,
        )
    else:
        # Large remote PDF or local file: per-page image mode
        use_image_mode = True
        _conversion_metadata["mode"] = "page-images"
        reason = (
            f"large remote PDF ({file_size / (1024*1024):.1f}MB > {SIZE_THRESHOLD_MB}MB threshold)"
            if source_url
            else "local file (base64 PDF not supported by API)"
        )
        print(f"GLM-OCR: Using per-page image mode ({reason})", file=sys.stderr)

    cached_page_images = None  # Set by image mode for bbox crop reuse

    if use_image_mode:
        # --- Per-page image mode ---
        # Page concurrency is overridable via GLM_OCR_MAX_WORKERS (default 10).
        # Lower it (e.g. 3) when the GLM key rate-limits a large multi-page PDF at
        # the default 10-way fan-out, which fails the whole conversion.
        _glm_workers = int(os.environ.get("GLM_OCR_MAX_WORKERS", "10") or "10")
        merged_md, merged_layout, merged_data_info, total_usage, cached_page_images = (
            _glm_ocr_via_page_images(
                pdf_path, auth_token, page_count, render_scale=3.0, debug=debug,
                max_workers=_glm_workers,
            )
        )
        if total_usage:
            _conversion_metadata["usage"] = total_usage

        # Build a synthetic result dict for the bbox extraction pipeline below
        result = {
            "md_results": merged_md,
            "layout_details": merged_layout,
            "data_info": merged_data_info,
        }
        markdown_content = merged_md
    else:
        # --- URL direct mode: single API call (with response caching in debug mode) ---
        cache_path = pdf_path.with_suffix(".glm-ocr.json")
        if debug and cache_path.exists():
            print(
                f"GLM-OCR: Loading cached response from {cache_path.name}",
                file=sys.stderr,
            )
            result = json.loads(cache_path.read_text(encoding="utf-8"))
        else:
            url = "https://open.bigmodel.cn/api/paas/v4/layout_parsing"
            headers = {
                "Authorization": auth_token,
                "Content-Type": "application/json",
            }
            payload = {
                "model": "glm-ocr",
                "file": file_value,
            }

            max_retries = 3
            last_error = None
            result = None

            for attempt in range(max_retries):
                try:
                    print(
                        f"GLM-OCR: Sending PDF to cloud API ({file_size / 1024:.0f} KB, "
                        f"{page_count} pages, attempt {attempt + 1}/{max_retries})",
                        file=sys.stderr,
                    )
                    response = requests.post(
                        url,
                        headers=headers,
                        json=payload,
                        timeout=300,  # 5 min timeout for cloud processing
                    )

                    if response.status_code == 429:
                        wait_time = 2 ** (attempt + 1)
                        print(
                            f"GLM-OCR: Rate limited, waiting {wait_time}s",
                            file=sys.stderr,
                        )
                        time.sleep(wait_time)
                        continue

                    if response.status_code != 200:
                        last_error = (
                            f"GLM-OCR API error: {response.status_code} - "
                            f"{response.text[:300]}"
                        )
                        if attempt < max_retries - 1:
                            time.sleep(2 ** attempt)
                            continue
                        output_error(last_error)

                    result = response.json()
                    break

                except requests.Timeout:
                    last_error = "GLM-OCR API request timed out (>5 minutes)"
                    if attempt < max_retries - 1:
                        time.sleep(2 ** attempt)
                        continue
                    output_error(
                        last_error, "The PDF may be too complex. Try a local engine."
                    )
                except requests.RequestException as e:
                    last_error = f"GLM-OCR API request failed: {e}"
                    if attempt < max_retries - 1:
                        time.sleep(2 ** attempt)
                        continue
                    output_error(last_error)
            else:
                output_error(last_error or "GLM-OCR API failed after all retries")

            if debug:
                cache_path.write_text(
                    json.dumps(result, ensure_ascii=False), encoding="utf-8"
                )
                print(
                    f"GLM-OCR: Debug: cached response to {cache_path.name}",
                    file=sys.stderr,
                )

        # Extract usage from URL-mode response
        glm_usage = result.get("usage", {})
        if glm_usage:
            _conversion_metadata["usage"] = {
                "prompt_tokens": glm_usage.get("prompt_tokens", 0),
                "completion_tokens": glm_usage.get("completion_tokens", 0),
                "total_tokens": glm_usage.get("total_tokens", 0),
            }

        markdown_content = result.get("md_results", "")
    if not markdown_content:
        output_error(
            "GLM-OCR returned empty markdown",
            "The PDF may be unreadable or in an unsupported format.",
        )

    # --- Extract images from PDF using GLM-OCR bbox coordinates ---
    #
    # GLM-OCR returns image refs as: ![alt](page=N,bbox=[x1, y1, x2, y2])
    # Coordinates use top-left origin in a pixel space whose dimensions
    # are given by result["data_info"]["pages"][N]["width"/"height"].
    # We render each needed page at a higher resolution for quality,
    # then proportionally map and crop the bbox region.
    # Also handles data URIs and remote URLs as fallback.

    assets_dir.mkdir(parents=True, exist_ok=True)
    image_counter = 0
    image_map = {}  # old_ref -> new_local_path

    # Parse all image references
    img_pattern = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
    image_refs = img_pattern.findall(markdown_content)

    # Detect bbox-style refs and collect which pages need rendering
    bbox_pattern = re.compile(
        r"^page=(\d+),bbox=\[(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\]$"
    )
    pages_needed = set()
    for _alt, ref in image_refs:
        m = bbox_pattern.match(ref)
        if m:
            pages_needed.add(int(m.group(1)))

    # Render needed pages once (lazy, cached by page number)
    rendered_pages = {}  # page_num -> (pil_image, glm_w, glm_h)
    glm_pages = result.get("data_info", {}).get("pages", [])

    if pages_needed and cached_page_images:
        # Image mode: reuse crop-scale renders (API coords based on render_scale,
        # mapping to crop_scale happens via glm_w/glm_h -> pil_img.size ratio)
        for pg_num in sorted(pages_needed):
            if pg_num not in cached_page_images:
                print(f"GLM-OCR: Warning: page {pg_num} not in cache, skipping", file=sys.stderr)
                continue
            pil_img = cached_page_images[pg_num]
            if pg_num < len(glm_pages):
                glm_w = glm_pages[pg_num]["width"]
                glm_h = glm_pages[pg_num]["height"]
            else:
                glm_w, glm_h = pil_img.size
            rendered_pages[pg_num] = (pil_img, glm_w, glm_h)
        print(
            f"GLM-OCR: Reusing {len(rendered_pages)} crop-scale page renders for image extraction",
            file=sys.stderr,
        )
    elif pages_needed:
        # URL mode: render from PDF at higher resolution for quality
        try:
            import pypdfium2 as pdfium

            pdf_doc = pdfium.PdfDocument(str(pdf_path))
            render_scale = 4.0  # High-quality output (288 DPI)

            for pg_num in sorted(pages_needed):
                if pg_num >= len(pdf_doc):
                    print(
                        f"GLM-OCR: Warning: page {pg_num} out of range, skipping",
                        file=sys.stderr,
                    )
                    continue
                pg = pdf_doc[pg_num]
                pg_w_pts = pg.get_width()
                pg_h_pts = pg.get_height()

                bitmap = pg.render(scale=render_scale)
                pil_img = bitmap.to_pil()

                # GLM-OCR reference dimensions from API response
                if pg_num < len(glm_pages):
                    glm_w = glm_pages[pg_num]["width"]
                    glm_h = glm_pages[pg_num]["height"]
                else:
                    glm_w = round(pg_w_pts * 10 / 3)
                    glm_h = round(pg_h_pts * 10 / 3)
                rendered_pages[pg_num] = (pil_img, glm_w, glm_h)

            pdf_doc.close()
            print(
                f"GLM-OCR: Rendered {len(rendered_pages)} pages for image extraction",
                file=sys.stderr,
            )
        except ImportError:
            print(
                "GLM-OCR: Warning: pypdfium2 not available, "
                "bbox images will not be extracted",
                file=sys.stderr,
            )
        except Exception as e:
            print(
                f"GLM-OCR: Warning: Failed to render PDF pages: {e}",
                file=sys.stderr,
            )

    # Extract each image
    for _alt_text, img_ref in image_refs:
        if img_ref in image_map:
            continue  # Already processed (dedup)

        image_counter += 1
        try:
            m = bbox_pattern.match(img_ref)
            if m:
                # --- Bbox reference: crop from rendered page ---
                pg_num = int(m.group(1))
                bx1, by1, bx2, by2 = (
                    int(m.group(2)),
                    int(m.group(3)),
                    int(m.group(4)),
                    int(m.group(5)),
                )

                if pg_num not in rendered_pages:
                    image_counter -= 1
                    continue

                pil_img, glm_w, glm_h = rendered_pages[pg_num]
                img_w, img_h = pil_img.size

                # Map GLM-OCR coords to our render resolution.
                # GLM-OCR uses top-left origin, same as image coordinates.
                # Add padding (min of 2% bbox size, 15px) to compensate for
                # tight API bbox bounds, clamped to image edges.
                sx = img_w / glm_w
                sy = img_h / glm_h
                _MAX_PAD_PX = 15
                pad_x = min(round((bx2 - bx1) * 0.02 * sx), _MAX_PAD_PX)
                pad_y = min(round((by2 - by1) * 0.02 * sy), _MAX_PAD_PX)
                crop_box = (
                    max(0, round(bx1 * sx) - pad_x),
                    max(0, round(by1 * sy) - pad_y),
                    min(img_w, round(bx2 * sx) + pad_x),
                    min(img_h, round(by2 * sy) + pad_y),
                )

                cropped = pil_img.crop(crop_box)
                if cropped.width < 1 or cropped.height < 1:
                    image_counter -= 1
                    continue

                # For bbox crops there is no "source" format — default to png
                crop_format = "png" if image_format == "source" else image_format
                target_ext = get_image_extension(
                    crop_format, ".png", default_for_source=".png"
                )
                img_name = f"image_{image_counter:03d}{target_ext}"
                img_path = assets_dir / img_name

                save_pil_image(
                    cropped, img_path, crop_format, image_quality, image_lossless
                )
                image_map[img_ref] = f"./assets/{img_name}"

            elif img_ref.startswith("data:") and ";base64," in img_ref:
                # --- Data URI ---
                header_part, b64data = img_ref.split(";base64,", 1)
                mime = header_part.split(":", 1)[1] if ":" in header_part else "image/png"
                ext_map = {
                    "image/png": ".png", "image/jpeg": ".jpg",
                    "image/webp": ".webp", "image/gif": ".gif",
                }
                source_ext = ext_map.get(mime, ".png")
                img_bytes = base64.b64decode(b64data)

                target_ext = get_image_extension(
                    image_format, source_ext, default_for_source=".png"
                )
                img_name = f"image_{image_counter:03d}{target_ext}"
                img_path = assets_dir / img_name

                save_image_bytes(
                    img_bytes, img_path, image_format, image_quality, image_lossless
                )
                image_map[img_ref] = f"./assets/{img_name}"

            elif img_ref.startswith(("http://", "https://")):
                # --- Remote URL ---
                img_resp = requests.get(img_ref, timeout=30)
                img_resp.raise_for_status()
                img_bytes = img_resp.content
                content_type = img_resp.headers.get("Content-Type", "")
                if "png" in content_type:
                    source_ext = ".png"
                elif "jpeg" in content_type or "jpg" in content_type:
                    source_ext = ".jpg"
                elif "webp" in content_type:
                    source_ext = ".webp"
                else:
                    source_ext = Path(urlparse(img_ref).path).suffix or ".png"

                target_ext = get_image_extension(
                    image_format, source_ext, default_for_source=".png"
                )
                img_name = f"image_{image_counter:03d}{target_ext}"
                img_path = assets_dir / img_name

                save_image_bytes(
                    img_bytes, img_path, image_format, image_quality, image_lossless
                )
                image_map[img_ref] = f"./assets/{img_name}"

            else:
                image_counter -= 1
                continue

        except Exception as e:
            print(
                f"GLM-OCR: Warning: Failed to extract image {image_counter}: {e}",
                file=sys.stderr,
            )
            image_counter -= 1

    # --- Rewrite image paths in markdown ---
    def replace_img_ref(match):
        alt_text = match.group(1)
        old_ref = match.group(2)
        if old_ref in image_map:
            return f"![{alt_text}]({image_map[old_ref]})"
        return match.group(0)

    markdown_content = re.sub(
        r"!\[([^\]]*)\]\(([^)]+)\)",
        replace_img_ref,
        markdown_content,
    )

    detected_title = extract_title_from_markdown(markdown_content)
    return markdown_content, detected_title


# ============================================================================
# DeepSeek-OCR Backend (Novita cloud, OpenAI-compatible vision)
# ============================================================================
#
# DeepSeek-OCR-2 is a single-image grounding OCR model served on Novita via the
# OpenAI-compatible chat.completions endpoint. It does NOT accept PDFs — we
# render each page to an image and OCR pages concurrently (mirrors GLM-OCR's
# per-page mode). In grounding mode the model emits typed blocks:
#
#     label[[x1, y1, x2, y2]]\n<text/LaTeX content>
#
# where coords are normalized to 0-1000 over the original page. A block may
# carry MULTIPLE bboxes: label[[x1,y1,x2,y2], [x1,y1,x2,y2]] (one logical block
# spanning two regions). `image`-type blocks are figure regions (empty text) —
# we crop them from a high-res page render. Every other block keeps its text.
# Block header: label[[x1,y1,x2,y2]] with coords normalized 0-1000. A block may
# carry MULTIPLE bboxes: label[[x1,y1,x2,y2], [x1,y1,x2,y2]]. The header is
# ANCHORED on a 4-integer coord set so in-text letter-adjacent double brackets
# (LaTeX/tensor indexing W[[i]], footnote ref[[1]], code f[[x]]) can NEVER be
# mis-parsed as a block (that loosened-regex bug truncated real paper text).
_DEEPSEEK_INT4 = r"\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*\d+"
_DEEPSEEK_BLOCK_RE = re.compile(
    r"([A-Za-z_]+)\[\[\s*("
    + _DEEPSEEK_INT4
    + r"(?:\s*\]\s*,\s*\[\s*"
    + _DEEPSEEK_INT4
    + r")*)\s*\]\]"
)
_DEEPSEEK_COORDS_RE = re.compile(r"(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)")
# echoed chat-template / grounding control tokens that occasionally leak
_DEEPSEEK_ECHO_RE = re.compile(r"<\|?/?(?:image|grounding|ref|det)\|?>|<image>")
# an HTML <table> that lost its row/cell structure (no <tr>) — unparseable blob
_DEEPSEEK_TABLE_RE = re.compile(r"<table\b.*?</table>", re.IGNORECASE | re.DOTALL)
_DEEPSEEK_ENDPOINT = (
    os.environ.get("NOVITA_BASE_URL", "https://api.novita.ai/openai").rstrip("/")
    + "/chat/completions"
)
_DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_OCR_MODEL", "deepseek/deepseek-ocr-2")
_DEEPSEEK_PROMPT = "<|grounding|>Convert the document to markdown."
_DEEPSEEK_PROMPT_PLAIN = "Convert the document to markdown."
# Novita caps deepseek-ocr output at 8192; 8192 itself flakes a 400, so use 8000.
_DEEPSEEK_MAX_TOKENS = int(os.environ.get("DEEPSEEK_OCR_MAX_TOKENS", "8000") or "8000")
# PDFium is not thread-safe; serialize every render through this lock.
import threading as _threading  # noqa: E402
_DEEPSEEK_PDF_LOCK = _threading.Lock()


# --- Global cross-process OCR-request concurrency limit (optional) -----------
# When OCR_GLOBAL_SLOTS (>=1) + OCR_SLOT_DIR are set, every per-page deepseek OCR
# HTTP request acquires one of N shared slot files before firing, so the AGGREGATE
# in-flight request count across ALL concurrent paper subprocesses is bounded to N
# (the cloud provider's concurrency cap). Single-paper and multi-paper runs draw
# from the SAME pool: a lone paper can use all N slots; many papers share them.
# Unset => no-op (standalone skill behaves exactly as before). Mirrors cortex's
# proven ingest_slots file-semaphore; a slot whose holder PID is dead is
# reclaimable (cortex SIGKILLs the subprocess group on timeout, skipping release).
import contextlib as _contextlib  # noqa: E402


def _ocr_slots_cfg():
    try:
        n = int(os.environ.get("OCR_GLOBAL_SLOTS", "") or "0")
    except ValueError:
        n = 0
    d = (os.environ.get("OCR_SLOT_DIR", "") or "").strip()
    return (n, d) if (n >= 1 and d) else (0, "")


def _ocr_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _ocr_claim(path: str) -> bool:
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    except OSError:
        return False
    try:
        os.write(fd, str(os.getpid()).encode())
    finally:
        os.close(fd)
    return True


def _ocr_slot_dead(path: str) -> bool:
    try:
        pid = int((Path(path).read_text(encoding="utf-8").strip() or "0"))
    except (OSError, ValueError):
        return False
    return pid > 0 and not _ocr_pid_alive(pid)


def _ocr_reclaim_dead(slots_dir: Path, n: int) -> None:
    """Unlink dead-holder slots, serialized behind a self-healing reclaim mutex."""
    lock = str(slots_dir / ".reclaim.lock")
    if not _ocr_claim(lock):
        if not _ocr_slot_dead(lock):
            return  # another worker is reclaiming
        try:
            os.unlink(lock)
        except OSError:
            pass
        if not _ocr_claim(lock):
            return
    try:
        for i in range(n):
            sp = str(slots_dir / f"ocr-slot-{i}")
            if _ocr_slot_dead(sp):
                try:
                    os.unlink(sp)
                except OSError:
                    pass
    finally:
        try:
            os.unlink(lock)
        except OSError:
            pass


@_contextlib.contextmanager
def _ocr_global_slot():
    """Hold one global OCR slot for the duration of a page request, or no-op when
    unconfigured. Degrades to UNTHROTTLED (never deadlocks) if no slot frees in
    time — the limit is best-effort throttling, not a correctness invariant."""
    n, d = _ocr_slots_cfg()
    if not n:
        yield
        return
    slots_dir = Path(d)
    try:
        slots_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        yield
        return
    timeout = float(os.environ.get("OCR_SLOT_ACQUIRE_TIMEOUT", "180") or "180")
    deadline = time.time() + timeout
    held = None
    while time.time() < deadline:
        for i in range(n):
            sp = str(slots_dir / f"ocr-slot-{i}")
            if _ocr_claim(sp):
                held = sp
                break
        if held is not None:
            break
        _ocr_reclaim_dead(slots_dir, n)  # free crashed/killed holders, then retry
        time.sleep(0.3)
    if held is None:
        print("DeepSeek-OCR: global slot acquire timed out — proceeding unthrottled",
              file=sys.stderr)
        yield
        return
    try:
        yield
    finally:
        try:
            os.unlink(held)
        except OSError:
            pass


def _deepseek_chat(b64, prompt, api_key, page_num, total_pages):
    """One grounding/plain OCR request (with retries). Returns (content, usage, finish_reason)."""
    import requests

    payload = {
        "model": _DEEPSEEK_MODEL,
        "temperature": 0.0,
        "max_tokens": _DEEPSEEK_MAX_TOKENS,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            {"type": "text", "text": prompt},
        ]}],
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    max_retries = 3
    max_rate_limit_retries = 6  # 429s get more patience (backoff 2,4,8,16,32,64s)
    err_attempts = 0
    rl_attempts = 0
    last_error = None
    while err_attempts < max_retries and rl_attempts < max_rate_limit_retries:
        try:
            with _ocr_global_slot():  # global cross-process concurrency cap (optional)
                resp = requests.post(_DEEPSEEK_ENDPOINT, headers=headers, json=payload, timeout=180)
            if resp.status_code == 429:
                rl_attempts += 1
                wait = 2 ** rl_attempts
                print(
                    f"DeepSeek-OCR: Rate limited on page {page_num + 1}, waiting {wait}s "
                    f"({rl_attempts}/{max_rate_limit_retries})",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                err_attempts += 1
                last_error = (
                    f"DeepSeek-OCR API error on page {page_num + 1}: "
                    f"{resp.status_code} - {resp.text[:200]}"
                )
                if err_attempts < max_retries:
                    time.sleep(2 ** err_attempts)
                    continue
                output_error(last_error)
            j = resp.json()
            choice = j.get("choices", [{}])[0]
            content = choice.get("message", {}).get("content", "") or ""
            finish = choice.get("finish_reason", "")
            return content, (j.get("usage", {}) or {}), finish
        except requests.Timeout:
            err_attempts += 1
            last_error = f"DeepSeek-OCR: Page {page_num + 1} timed out (>180s)"
            if err_attempts < max_retries:
                time.sleep(2 ** err_attempts)
                continue
            output_error(last_error)
        except requests.RequestException as e:
            err_attempts += 1
            last_error = f"DeepSeek-OCR: Page {page_num + 1} request failed: {e}"
            if err_attempts < max_retries:
                time.sleep(2 ** err_attempts)
                continue
            output_error(last_error)
    output_error(last_error or f"DeepSeek-OCR: Page {page_num + 1} failed after all retries")
    return "", {}, ""  # unreachable (output_error exits)


def _deepseek_ocr_page(shared_doc, page_num, total_pages, api_key, ocr_scale):
    """Render (lock-serialized) + OCR one page. Returns (page_num, content, usage, truncated).

    PDFium is NOT thread-safe — even across separate documents — so ALL rendering
    is serialized under _DEEPSEEK_PDF_LOCK; only the (slow) OCR HTTP call runs
    concurrently. Rendering one page just-in-time keeps peak RAM ~workers images.
    """
    import base64

    with _DEEPSEEK_PDF_LOCK:
        pil_img = shared_doc[page_num].render(scale=ocr_scale).to_pil()
        buf = io.BytesIO()
        pil_img.save(buf, format="JPEG", quality=90)
        del pil_img  # detach from PDFium-backed bitmap before releasing the lock
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    print(
        f"DeepSeek-OCR: Page {page_num + 1}/{total_pages} (~{len(b64) * 3 // 4 // 1024} KB)",
        file=sys.stderr,
    )

    content, usage, finish = _deepseek_chat(b64, _DEEPSEEK_PROMPT, api_key, page_num, total_pages)
    truncated = finish == "length"
    if truncated:
        # Output hit the token cap. Grounding mode spends tokens on bboxes; retry
        # once in plain mode (fewer tokens, no bbox overhead) to recover content.
        print(
            f"DeepSeek-OCR: Page {page_num + 1} hit the {_DEEPSEEK_MAX_TOKENS}-token cap "
            f"(finish=length); retrying in plain mode to recover text",
            file=sys.stderr,
        )
        p_content, p_usage, p_finish = _deepseek_chat(
            b64, _DEEPSEEK_PROMPT_PLAIN, api_key, page_num, total_pages
        )
        # Merge only the integer token counters; Novita usage also carries
        # *_details keys whose values are None (None + None would crash).
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            usage[k] = (usage.get(k) or 0) + (p_usage.get(k) or 0)
        if len(p_content) >= len(content):
            # Plain retry recovered at least as much — keep it; truncated iff it
            # too hit the cap.
            content = p_content
            truncated = p_finish == "length"
        else:
            # Kept the (truncated) grounding content — still truncated.
            truncated = True
    return page_num, content, usage, truncated


def _deepseek_crop_is_meaningful(pil_crop) -> bool:
    """Reject near-blank crops (model occasionally marks empty whitespace as image)."""
    try:
        lo, hi = pil_crop.convert("L").getextrema()
        return (hi - lo) > 12
    except Exception:
        return True


def _deepseek_flag_bad_tables(md):
    """Prefix a loud warning before any <table> that lost its <tr> row structure,
    so an unparseable table blob is never emitted silently. Returns (md, n_flagged)."""
    n = [0]

    def repl(m):
        block = m.group(0)
        if "<tr" not in block.lower():
            n[0] += 1
            return (
                "\n<!-- WARNING DeepSeek-OCR: the table below lost its row/column "
                "structure (no <tr>/<td>); verify against the source PDF -->\n" + block
            )
        return block

    out = _DEEPSEEK_TABLE_RE.sub(repl, md)
    return out, n[0]


def _deepseek_page_has_figure(raw):
    """True if the page's raw output contains at least one croppable image block."""
    for m in _DEEPSEEK_BLOCK_RE.finditer(raw or ""):
        label = m.group(1).lower()
        if ("image" in label) or label in ("figure", "picture", "chart"):
            return True
    return False


def _deepseek_parse_page(
    raw, crop_img, assets_dir, image_format, image_quality, image_lossless, counter
):
    """Parse typed grounding blocks into markdown; crop `image` blocks from crop_img.

    crop_img may be None when the page has no figure blocks. counter is a
    single-element list (shared mutable image counter across pages).
    """
    raw = raw or ""
    matches = list(_DEEPSEEK_BLOCK_RE.finditer(raw))
    if not matches:
        # No grounding blocks — model returned plain markdown (or a mode whose
        # coords we don't recognize). Keep the text; figures, if any, are not
        # cropped. The caller logs this so figure-loss is never silent.
        return _DEEPSEEK_ECHO_RE.sub("", raw).strip()

    crop_format = "png" if image_format == "source" else image_format
    target_ext = get_image_extension(crop_format, ".png", default_for_source=".png")
    cw, ch = crop_img.size if crop_img is not None else (0, 0)
    parts = []
    for i, m in enumerate(matches):
        label = m.group(1).lower()
        coord_sets = _DEEPSEEK_COORDS_RE.findall(m.group(2))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        content = _DEEPSEEK_ECHO_RE.sub("", raw[start:end]).strip()
        is_figure = ("image" in label) or label in ("figure", "picture", "chart")
        if is_figure:
            cropped_ok = False
            if coord_sets and crop_img is not None:
                # Union of all bboxes for this block, mapped 0-1000 -> crop pixels.
                xs1 = [int(c[0]) for c in coord_sets]
                ys1 = [int(c[1]) for c in coord_sets]
                xs2 = [int(c[2]) for c in coord_sets]
                ys2 = [int(c[3]) for c in coord_sets]
                cx1 = max(0, round(min(xs1) / 1000 * cw))
                cy1 = max(0, round(min(ys1) / 1000 * ch))
                cx2 = min(cw, round(max(xs2) / 1000 * cw))
                cy2 = min(ch, round(max(ys2) / 1000 * ch))
                if cx2 - cx1 >= 8 and cy2 - cy1 >= 8:
                    crop = crop_img.crop((cx1, cy1, cx2, cy2))
                    if _deepseek_crop_is_meaningful(crop):
                        counter[0] += 1
                        name = f"image_{counter[0]:03d}{target_ext}"
                        save_pil_image(
                            crop, assets_dir / name, crop_format, image_quality, image_lossless
                        )
                        parts.append(f"![Figure {counter[0]}](./assets/{name})")
                        cropped_ok = True
            if not cropped_ok:
                # Figure region we could not crop — leave a visible placeholder so
                # the figure is never silently dropped.
                parts.append("<!-- figure region (not croppable) -->")
            if content:
                parts.append(content)
        elif content:
            parts.append(content)
    return "\n\n".join(parts)


def convert_with_deepseek_ocr(
    pdf_path, assets_dir, image_format, image_quality, image_lossless, debug=False
):
    """Convert a PDF to Markdown via DeepSeek-OCR-2 on Novita (per-page grounding OCR).

    DeepSeek-OCR is a single-image model: each worker renders its OWN page and
    OCRs it in grounding mode (typed blocks with 0-1000 bboxes). `image` blocks
    are cropped from a high-res render; all other text/LaTeX is kept. Truncation,
    empty pages, malformed tables, and uncroppable figures are surfaced (never
    silently dropped). Requires NOVITA_API_KEY (Novita OpenAI-compatible key).

    Returns (markdown_content, detected_title).
    """
    import pypdfium2 as pdfium

    global _conversion_metadata

    api_key = os.environ.get("NOVITA_API_KEY", "").strip()
    if not api_key:
        output_error(
            "NOVITA_API_KEY is not set",
            "Set NOVITA_API_KEY (Novita OpenAI-compatible key) in the environment "
            "or paper-ingestion/.env. Get one at https://novita.ai",
        )

    image_format = normalize_image_format(image_format)
    image_quality = clamp_image_quality(image_quality)
    page_count = get_pdf_page_count(pdf_path)
    if page_count > 100:
        output_error(
            f"PDF has {page_count} pages (DeepSeek-OCR cap: 100 pages)",
            "Try --engine mineru or --engine docling for very large PDFs.",
        )

    workers = int(os.environ.get("DEEPSEEK_OCR_MAX_WORKERS", "4") or "4")
    ocr_scale = float(os.environ.get("DEEPSEEK_OCR_RENDER_SCALE", "3.0") or "3.0")
    crop_scale = float(os.environ.get("DEEPSEEK_OCR_CROP_SCALE", "4.0") or "4.0")

    # One shared PdfDocument for the whole run: rendered lock-serialized by the
    # OCR workers (PDFium isn't thread-safe), then reused single-threaded for crops.
    shared_doc = pdfium.PdfDocument(str(pdf_path))
    try:
        actual_pages = min(page_count, len(shared_doc)) if page_count else len(shared_doc)

        _conversion_metadata = {
            "backend": "deepseek-ocr",
            "mode": "cloud",
            "model": _DEEPSEEK_MODEL,
            "page_count": page_count,
            "image_format": image_format,
            "image_quality": image_quality,
            "image_lossless": image_lossless,
        }

        print(
            f"DeepSeek-OCR: OCR {actual_pages} pages with {workers} concurrent workers",
            file=sys.stderr,
        )
        results = {}
        truncated_pages = []
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    _deepseek_ocr_page, shared_doc, pg, actual_pages, api_key, ocr_scale
                ): pg
                for pg in range(actual_pages)
            }
            for future in as_completed(futures):
                pg, content, usage, truncated = future.result()
                results[pg] = content
                if truncated:
                    truncated_pages.append(pg)
                for k in total_usage:
                    total_usage[k] += usage.get(k, 0)

        if total_usage["total_tokens"]:
            _conversion_metadata["usage"] = total_usage

        assets_dir.mkdir(parents=True, exist_ok=True)
        counter = [0]
        md_parts = []
        empty_pages = []
        plain_pages = []
        bad_tables = 0
        # Single-threaded merge (all workers done): render the crop image lazily,
        # only for pages with a figure, then discard it (peak RAM ~1 crop image).
        for pg in range(actual_pages):
            raw = results.get(pg, "")
            if not (raw or "").strip():
                empty_pages.append(pg)
                md_parts.append(
                    f"<!-- WARNING DeepSeek-OCR: page {pg + 1} returned no content -->"
                )
                continue
            if not _DEEPSEEK_BLOCK_RE.search(raw):
                plain_pages.append(pg)  # no grounding blocks (figures not cropped)
            crop_img = None
            if _deepseek_page_has_figure(raw):
                crop_img = shared_doc[pg].render(scale=crop_scale).to_pil()
            page_md = _deepseek_parse_page(
                raw, crop_img, assets_dir,
                image_format, image_quality, image_lossless, counter,
            )
            page_md, flagged = _deepseek_flag_bad_tables(page_md)
            bad_tables += flagged
            if truncated_pages and pg in truncated_pages:
                page_md += (
                    f"\n\n<!-- WARNING DeepSeek-OCR: page {pg + 1} output hit the token "
                    f"cap and may be truncated -->"
                )
            if page_md.strip():
                md_parts.append(page_md)
    finally:
        shared_doc.close()
    markdown_content = "\n\n".join(md_parts)

    # Surface every degradation so nothing fails silently.
    if truncated_pages:
        print(
            f"DeepSeek-OCR: WARNING {len(truncated_pages)} page(s) may be truncated "
            f"(hit token cap): pages {[p + 1 for p in truncated_pages]}",
            file=sys.stderr,
        )
    if empty_pages:
        print(
            f"DeepSeek-OCR: WARNING {len(empty_pages)} page(s) returned empty: "
            f"pages {[p + 1 for p in empty_pages]}",
            file=sys.stderr,
        )
    if plain_pages:
        print(
            f"DeepSeek-OCR: WARNING {len(plain_pages)} page(s) had no grounding blocks "
            f"(plain text, figures not cropped): pages {[p + 1 for p in plain_pages]}",
            file=sys.stderr,
        )
    if bad_tables:
        print(
            f"DeepSeek-OCR: WARNING {bad_tables} table(s) lost row/column structure "
            f"(flagged inline)",
            file=sys.stderr,
        )
    _conversion_metadata["degradations"] = {
        "truncated_pages": [p + 1 for p in truncated_pages],
        "empty_pages": [p + 1 for p in empty_pages],
        "plain_pages": [p + 1 for p in plain_pages],
        "malformed_tables": bad_tables,
    }

    if not markdown_content.strip():
        output_error(
            "DeepSeek-OCR returned empty markdown",
            "The PDF may be unreadable, or the Novita endpoint rejected the pages.",
        )

    detected_title = extract_title_from_markdown(markdown_content)
    return markdown_content, detected_title


# ============================================================================
# File Organization
# ============================================================================


def setup_paper_directory(
    pdf_path: Path,
    markdown_content: str,
    engine: str,
    detected_title: str | None,
    output_dir: str | None = None,
    allow_duplicate: bool = False,
) -> dict:
    """
    Organize files with timestamped folder naming.

    Structure:
      {cwd}/{YYYYMMDD}-{Sanitized_Title}/
        reference.pdf    - Original PDF (copied)
        full_text.md     - Converted Markdown with YAML frontmatter
        notes.md         - Empty file for analysis notes
        assets/          - Extracted images (if any)
    """
    today = datetime.now()
    date_str = today.strftime("%Y%m%d")
    date_iso = today.strftime("%Y-%m-%d")

    # Use detected title or fall back to filename
    title_source = detected_title if detected_title else pdf_path.stem
    sanitized_title = sanitize_filename(title_source)

    output_root = get_output_root(output_dir)

    # Check for duplicates
    if not allow_duplicate and check_duplicate(sanitized_title, output_root):
        output_error(
            f"Duplicate detected: A folder with title '{sanitized_title}' already exists",
            "Remove the existing folder or rename if you want to re-ingest",
        )

    # Create timestamped folder name
    folder_name = f"{date_str}-{sanitized_title}"
    paper_dir = output_root / folder_name

    # Create directory
    paper_dir.mkdir(parents=True, exist_ok=True)

    # Copy original PDF
    reference_pdf = paper_dir / "reference.pdf"
    shutil.copy2(pdf_path, reference_pdf)

    # Create YAML frontmatter
    display_title = (
        detected_title if detected_title else sanitized_title.replace("_", " ")
    )
    frontmatter = f"""---
title: "{display_title}"
date_ingested: {date_iso}
source_pdf: reference.pdf
conversion_engine: {engine}
tags:
  - paper
aliases: []
---

"""

    # Save Markdown with frontmatter
    full_text_path = paper_dir / "full_text.md"
    full_text_path.write_text(frontmatter + markdown_content, encoding="utf-8")

    # Create empty notes file
    notes_path = paper_dir / "notes.md"
    if not notes_path.exists():
        notes_path.write_text(f"# Notes: {display_title}\n\n", encoding="utf-8")

    return {
        "paper_dir": str(paper_dir),
        "markdown_path": str(full_text_path),
        "reference_pdf": str(reference_pdf),
        "notes_path": str(notes_path),
        "title": sanitized_title,
        "date": date_iso,
    }


# ============================================================================
# Main Entry Point
# ============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Ingest PDF paper and convert to Markdown"
    )
    start_time = time.time()
    parser.add_argument(
        "pdf_source", type=str, help="Path to local PDF file OR URL to download"
    )
    parser.add_argument(
        "--engine",
        type=str,
        choices=["mineru", "docling", "glm-ocr", "deepseek-ocr"],
        default="glm-ocr",
        help="Conversion engine: glm-ocr (default, cloud API, no GPU needed), deepseek-ocr (Novita cloud, cheap vision OCR), mineru (highest quality, GPU), docling (fallback, fast)",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=str,
        default=None,
        help="Output directory (default: current working directory)",
    )
    parser.add_argument(
        "--images-scale",
        type=float,
        default=4.0,
        help="Image scale factor for extraction (1.0 ~= 72 DPI). Use >1 for higher resolution.",
    )
    parser.add_argument(
        "--image-format",
        type=str,
        default="webp",
        choices=["source", "png", "jpg", "jpeg", "webp"],
        help="Output image format. 'webp' (default) gives best compression. 'source' keeps original format.",
    )
    parser.add_argument(
        "--image-quality",
        type=int,
        default=95,
        help="Image quality for lossy formats (1-100).",
    )
    parser.add_argument(
        "--image-lossless",
        action="store_true",
        help="Use lossless encoding when supported (webp).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow overwriting when a folder with the same title exists",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Keep intermediate files (e.g. GLM-OCR raw JSON response) for debugging.",
    )

    args = parser.parse_args()

    try:
        # Handle URL or local path
        temp_pdf = None
        source_is_url = is_url(args.pdf_source)
        glm_source_url = None  # URL to pass directly to GLM-OCR API
        if source_is_url:
            normalized_url = normalize_arxiv_url(args.pdf_source)
            if normalized_url != args.pdf_source:
                print(
                    f"GLM-OCR: Converted arxiv abstract URL to PDF URL: {normalized_url}",
                    file=sys.stderr,
                )
            glm_source_url = normalized_url
            pdf_path = download_pdf(normalized_url)
            temp_pdf = pdf_path.parent  # Remember temp dir for cleanup
        else:
            pdf_path = Path(args.pdf_source).resolve()
            if not pdf_path.exists():
                output_error(f"File not found: {pdf_path}")
            if not pdf_path.suffix.lower() == ".pdf":
                output_error(f"Not a PDF file: {pdf_path}")

        image_format = normalize_image_format(args.image_format)
        image_quality = clamp_image_quality(args.image_quality)
        image_lossless = args.image_lossless

        # Convert based on engine
        engine = args.engine
        temp_assets = Path(tempfile.mkdtemp()) / "assets"
        temp_assets.mkdir(parents=True, exist_ok=True)

        if engine == "mineru":
            markdown_content, detected_title = convert_with_mineru(
                pdf_path,
                temp_assets,
                image_format,
                image_quality,
                image_lossless,
            )
        elif engine == "docling":
            markdown_content, detected_title = convert_with_docling(
                pdf_path,
                temp_assets,
                args.images_scale,
                image_format,
                image_quality,
                image_lossless,
            )
        elif engine == "glm-ocr":
            markdown_content, detected_title = convert_with_glm_ocr(
                pdf_path,
                temp_assets,
                image_format,
                image_quality,
                image_lossless,
                debug=args.debug,
                source_url=glm_source_url,
            )
        elif engine == "deepseek-ocr":
            markdown_content, detected_title = convert_with_deepseek_ocr(
                pdf_path,
                temp_assets,
                image_format,
                image_quality,
                image_lossless,
                debug=args.debug,
            )

        # Normalize math delimiters to $...$ / $$...$$
        markdown_content = apply_outside_code_blocks(
            markdown_content, normalize_math_delimiters
        )
        # Wrap common inline math patterns (subscripts, greek letters, etc.)
        markdown_content = wrap_inline_math(markdown_content)

        # Fix common OCR-introduced LaTeX formula errors
        from fix_formulas import fix_latex_formulas
        markdown_content, fix_log = fix_latex_formulas(markdown_content)
        if fix_log:
            print(
                f"LaTeX formula fixes applied ({len(fix_log)} changes)",
                file=sys.stderr,
            )
            if args.debug:
                for entry in fix_log[:10]:
                    print(f"  {entry}", file=sys.stderr)

        resolved_title = resolve_paper_title(detected_title, markdown_content, pdf_path)
        if not resolved_title and not source_is_url:
            resolved_title = pdf_path.stem
        if not resolved_title:
            resolved_title = "untitled_paper"

        # Organize files
        paths = setup_paper_directory(
            pdf_path,
            markdown_content,
            engine,
            resolved_title,
            args.output_dir,
            args.force,
        )

        # Move assets to final location
        if temp_assets and temp_assets.exists():
            final_assets = Path(paths["paper_dir"]) / "assets"
            if any(temp_assets.iterdir()):
                shutil.copytree(temp_assets, final_assets, dirs_exist_ok=True)
            shutil.rmtree(temp_assets.parent, ignore_errors=True)

        # Output success JSON
        output_data = {
            "status": "success",
            "markdown_path": paths["markdown_path"],
            "engine_used": engine,
            "title": paths["title"],
            "date": paths["date"],
            "paper_dir": paths["paper_dir"],
        }
        meta = get_conversion_metadata()
        if "usage" in meta:
            output_data["usage"] = meta["usage"]
        output_json(output_data)

    finally:
        # Cleanup temp download
        if temp_pdf:
            shutil.rmtree(temp_pdf, ignore_errors=True)

        elapsed_time = time.time() - start_time

        # Enhanced logging with conversion details
        print("\n" + "=" * 50, file=sys.stderr)
        print("Conversion Summary", file=sys.stderr)
        print("=" * 50, file=sys.stderr)

        # Get conversion metadata
        if engine == "mineru":
            meta = get_conversion_metadata()
            mode = meta.get("mode", "cli")
            print(f"  Backend: MinerU ({mode} mode)", file=sys.stderr)
            if mode == "api":
                print(
                    f"  API Server: {meta.get('api_host')}:{meta.get('api_port')}",
                    file=sys.stderr,
                )
            else:
                print(f"  GPUs: {meta.get('gpus', 'default')}", file=sys.stderr)
                print(f"  Workers: {meta.get('num_workers', 1)}", file=sys.stderr)
            print(f"  Batch Ratio: {meta.get('batch_ratio', 8)}", file=sys.stderr)
            print(f"  Pages: {meta.get('page_count', 'unknown')}", file=sys.stderr)
            print(f"  Image Format: {meta.get('image_format', image_format)}", file=sys.stderr)
            print(f"  Image Quality: {meta.get('image_quality', image_quality)}", file=sys.stderr)
            if meta.get("image_lossless", image_lossless):
                print("  Image Lossless: true", file=sys.stderr)
        elif engine == "docling":
            print("  Backend: Docling", file=sys.stderr)
            print(f"  Images Scale: {args.images_scale}", file=sys.stderr)
            print(f"  Image Format: {image_format}", file=sys.stderr)
            print(f"  Image Quality: {image_quality}", file=sys.stderr)
            if image_lossless:
                print("  Image Lossless: true", file=sys.stderr)
        elif engine == "glm-ocr":
            meta = get_conversion_metadata()
            mode = meta.get("mode", "cloud")
            print(f"  Backend: GLM-OCR (cloud API, {mode})", file=sys.stderr)
            print(f"  Pages: {meta.get('page_count', 'unknown')}", file=sys.stderr)
            print(f"  File Size: {meta.get('file_size_mb', 'unknown')} MB", file=sys.stderr)
            usage = meta.get("usage")
            if usage:
                print(f"  Prompt Tokens:     {usage.get('prompt_tokens', 0):,}", file=sys.stderr)
                print(f"  Completion Tokens: {usage.get('completion_tokens', 0):,}", file=sys.stderr)
                print(f"  Total Tokens:      {usage.get('total_tokens', 0):,}", file=sys.stderr)
            print(f"  Image Format: {meta.get('image_format', image_format)}", file=sys.stderr)
            print(f"  Image Quality: {meta.get('image_quality', image_quality)}", file=sys.stderr)
            if meta.get("image_lossless", image_lossless):
                print("  Image Lossless: true", file=sys.stderr)
        elif engine == "deepseek-ocr":
            meta = get_conversion_metadata()
            print(f"  Backend: DeepSeek-OCR (Novita cloud, {meta.get('model')})", file=sys.stderr)
            print(f"  Pages: {meta.get('page_count', 'unknown')}", file=sys.stderr)
            usage = meta.get("usage")
            if usage:
                tot = usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
                print(f"  Prompt Tokens:     {usage.get('prompt_tokens', 0):,}", file=sys.stderr)
                print(f"  Completion Tokens: {usage.get('completion_tokens', 0):,}", file=sys.stderr)
                print(f"  Est. Cost: ${tot / 1e6 * 0.03:.5f} (@ $0.03/M in+out)", file=sys.stderr)
            print(f"  Image Format: {meta.get('image_format', image_format)}", file=sys.stderr)
            print(f"  Image Quality: {meta.get('image_quality', image_quality)}", file=sys.stderr)
            if meta.get("image_lossless", image_lossless):
                print("  Image Lossless: true", file=sys.stderr)

        print(f"  Total Time: {elapsed_time:.2f}s", file=sys.stderr)

        # Calculate pages per second if we have page count
        if engine in ("mineru", "glm-ocr", "deepseek-ocr"):
            meta = get_conversion_metadata()
            page_count = meta.get("page_count", 0)
            if page_count > 0 and elapsed_time > 0:
                pps = page_count / elapsed_time
                print(f"  Speed: {pps:.2f} pages/sec", file=sys.stderr)

        print("=" * 50, file=sys.stderr)


if __name__ == "__main__":
    main()
