"""Exercise exported paths and preserved paper content without running OCR."""

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ingest_paper


@pytest.mark.parametrize("title,short_name,filename", [
    ("Faster-WAM: Efficient Future Conditioning", None, "full_text-Faster-WAM.md"),
    ("LingBot-VA 2.0: Native Pretraining", None, "full_text-LingBot-VA_2.0.md"),
    ("Native Video-Action Pretraining", "LingBot-VA", "full_text-LingBot-VA.md"),
    ("Attention Is All You Need", None, "full_text-Attention_Is_All_You_Need.md"),
])
def test_export_uses_descriptive_original_without_changing_content(tmp_path, title, short_name, filename):
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"%PDF fixture")
    body = f"# {title}\n\n![Figure](./assets/figure.png)\n\n$x+y$\n"
    result = ingest_paper.setup_paper_directory(pdf, body, "glm-ocr", title,
        str(tmp_path / "output"), paper_name=short_name)
    original = Path(result["markdown_path"])
    assert original.name == filename
    assert original.read_text().endswith(body)
    assert Path(result["reference_pdf"]).read_bytes() == pdf.read_bytes()
    assert not (original.parent / "full_text.md").exists()
    assert result["paper_name"] in original.name


def test_cli_passes_verified_method_name_to_export(tmp_path, monkeypatch, capsys):
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"%PDF fixture")
    monkeypatch.setattr(ingest_paper, "convert_with_glm_ocr",
        lambda *args, **kwargs: ("# Native Pretraining\n\nA method.\n", "Native Pretraining"))
    monkeypatch.setattr(sys, "argv", ["ingest_paper.py", str(pdf),
        "--output-dir", str(tmp_path / "output"), "--paper-name", "LingBot-VA"])
    ingest_paper.main()
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "success"
    assert result["paper_name"] == "LingBot-VA"
    assert Path(result["markdown_path"]).name == "full_text-LingBot-VA.md"
    assert Path(result["markdown_path"]).is_file()


def test_force_does_not_create_two_originals_or_overwrite_notes(tmp_path):
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"original PDF")
    result = ingest_paper.setup_paper_directory(pdf, "original body", "glm-ocr", "Method",
        str(tmp_path / "output"))
    notes = Path(result["notes_path"])
    notes.write_text("User notes")
    pdf.write_bytes(b"changed PDF")
    with pytest.raises(SystemExit):
        ingest_paper.setup_paper_directory(pdf, "changed body", "glm-ocr", "Method",
            str(tmp_path / "output"), allow_duplicate=True, paper_name="Other")
    assert notes.read_text() == "User notes"
    assert Path(result["reference_pdf"]).read_bytes() == b"original PDF"
    assert Path(result["markdown_path"]).read_text().endswith("original body")


def test_names_are_bounded_and_cannot_escape_the_paper_directory(tmp_path):
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"fixture")
    result = ingest_paper.setup_paper_directory(pdf, "body", "glm-ocr", "Method",
        str(tmp_path / "output"), paper_name="../../" + "方法" * 100 + "/body")
    original = Path(result["markdown_path"])
    assert original.parent == Path(result["paper_dir"])
    assert len(original.name.encode("utf-8")) < 255
    assert original.is_file()


def test_lingbot_name_is_automatic_even_when_absent_from_arxiv_title(tmp_path):
    # Author-introduction excerpt: https://arxiv.org/abs/2601.21998
    title = "Causal World Modeling for Robot Control"
    body = (f"# {title}\n\n## Abstract\n\n"
            "Inspired by this, we introduce LingBot-VA, an autoregressive diffusion framework.\n")
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"fixture")
    result = ingest_paper.setup_paper_directory(pdf, body, "glm-ocr", title, str(tmp_path / "out"))
    assert Path(result["markdown_path"]).name == "full_text-LingBot-VA.md"
    assert result["paper_name_source"] == "author_introduction"
    assert "we introduce LingBot-VA" in result["paper_name_evidence"]
    assert title in Path(result["markdown_path"]).read_text()


@pytest.mark.parametrize("sentence,expected", [
    ("We present **LingBot-VA 2.0**, a new framework.", "LingBot-VA_2.0"),
    ("We introduce LingBot-\nVA, a framework.", "LingBot-VA"),
    ("We propose a causal framework called Robot-Cause for control.", "Robot-Cause"),
    ("We develop a control model (Robot-Cause) for manipulation.", "Robot-Cause"),
    ("Our framework, Robot-Cause, is evaluated below.", "Robot-Cause"),
])
def test_method_introduction_variants(sentence, expected):
    result = ingest_paper.resolve_paper_name("A Descriptive Formal Title", "## Abstract\n\n" + sentence)
    assert result["paper_name"] == expected
    assert result["paper_name_source"] == "author_introduction"


@pytest.mark.parametrize("body", [
    "## Abstract\nWe compare with LingBot-VA and Faster-WAM.\n",
    "## Abstract\nWe introduce a new approach.\n## Related Work\nWe present Baseline-X, in an earlier study.\n",
    "## Abstract\nWe introduce Method-A, a model. We propose Method-B, a framework.\n",
    "## Abstract\n```\nWe present Baseline-X, a model.\n```\n",
    "## Abstract\n> We introduce Baseline-X, a model.\n",
])
def test_baselines_quotes_and_ambiguous_names_fall_back_to_title(body):
    result = ingest_paper.resolve_paper_name("Causal World Modeling for Robot Control", body)
    assert result["paper_name"] == "Causal_World_Modeling_for_Robot_Control"
    assert result["paper_name_source"] == "title"


def test_explicit_verified_name_takes_priority_over_automatic_detection():
    result = ingest_paper.resolve_paper_name("Formal Title",
        "## Abstract\nWe introduce Method-A, a model.\n", "Requested-Method")
    assert result["paper_name"] == "Requested-Method"
    assert result["paper_name_source"] == "explicit"


def test_abstract_alias_beats_a_component_named_in_the_introduction():
    result = ingest_paper.resolve_paper_name("Pretraining for Robotic Manipulation",
        "## Abstract\nWe introduce Genie Envisioner Act 2.0 (GE-Act 2.0), a model.\n"
        "## 1 Introduction\nWe propose Component-X, an optimizer.\n")
    assert result["paper_name"] == "GE-Act_2.0"
    assert result["paper_name_source"] == "author_introduction"
