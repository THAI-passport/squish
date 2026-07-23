"""Per-tool behaviour.

Assertions check the OUTCOME, not merely that a file appeared. "Compress
produced a file" passes even when the file is a corrupt zero-page PDF; "the
output has the same page count and is smaller" does not.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import fitz
import pytest

import tools as T

has = lambda b: shutil.which(b) is not None  # noqa: E731
needs_gs = pytest.mark.skipif(not has("gs"), reason="ghostscript not installed")
needs_qpdf = pytest.mark.skipif(not has("qpdf"), reason="qpdf not installed")
needs_office = pytest.mark.skipif(not has("soffice"), reason="libreoffice not installed")


def pages_of(path: Path) -> int:
    d = fitz.open(path)
    n = d.page_count
    d.close()
    return n


def text_of(path: Path) -> str:
    d = fitz.open(path)
    t = "\n".join(p.get_text() for p in d)
    d.close()
    return t


# ------------------------------------------------------------- organize ---

def test_merge_concatenates_in_order(work, pdf, pdf2):
    r = T.merge(work, [pdf, pdf2], {})
    assert pages_of(r.path) == pages_of(pdf) + pages_of(pdf2)
    assert "Second document." in text_of(r.path)


def test_merge_rejects_single_file(work, pdf):
    with pytest.raises(T.ToolError, match="at least 2"):
        T.merge(work, [pdf], {})


def test_split_ranges_selects_only_named_pages(work, pdf):
    r = T.split(work, [pdf], {"mode": "ranges", "pages": "1,3"})
    assert pages_of(r.path) == 2
    assert "Page 2 of 3" not in text_of(r.path)


def test_split_every_page_yields_a_zip(work, pdf):
    r = T.split(work, [pdf], {"mode": "every"})
    with zipfile.ZipFile(r.path) as z:
        assert len(z.namelist()) == 3


def test_split_chunks_groups_pages(work, pdf):
    r = T.split(work, [pdf], {"mode": "chunks", "size": 2})
    with zipfile.ZipFile(r.path) as z:
        assert len(z.namelist()) == 2      # 3 pages -> [1-2], [3]


def test_remove_pages_keeps_the_rest(work, pdf):
    r = T.remove_pages(work, [pdf], {"pages": "2"})
    assert pages_of(r.path) == 2
    assert "CLASSIFIED-MARKER" not in text_of(r.path)


def test_remove_all_pages_is_rejected(work, pdf):
    with pytest.raises(T.ToolError, match="every page"):
        T.remove_pages(work, [pdf], {"pages": "1-3"})


def test_organize_applies_explicit_order(work, pdf):
    r = T.organize(work, [pdf], {"pages": "3,1,2"})
    d = fitz.open(r.path)
    assert "Page 3 of 3" in d[0].get_text()
    d.close()


def test_rotate_changes_only_selected_pages(work, pdf):
    r = T.rotate(work, [pdf], {"angle": 90, "pages": "1"})
    d = fitz.open(r.path)
    assert d[0].rotation == 90 and d[1].rotation == 0
    d.close()


def test_rotate_rejects_non_right_angles(work, pdf):
    with pytest.raises(T.ToolError, match="multiple of 90"):
        T.rotate(work, [pdf], {"angle": 45})


def test_split_bookmarks_uses_the_outline(work, bookmarked_pdf):
    r = T.split_bookmarks(work, [bookmarked_pdf], {"level": 1})
    with zipfile.ZipFile(r.path) as z:
        names = z.namelist()
    assert len(names) == 3
    assert any("Introduction" in n for n in names)


def test_split_bookmarks_without_outline_errors(work, pdf):
    with pytest.raises(T.ToolError, match="no bookmarks"):
        T.split_bookmarks(work, [pdf], {"level": 1})


def test_n_up_halves_the_sheet_count(work, pdf):
    r = T.n_up(work, [pdf], {"per_sheet": 2})
    assert pages_of(r.path) == 2           # 3 pages over 2 sheets


def test_n_up_rejects_odd_layouts(work, pdf):
    with pytest.raises(T.ToolError, match="2 or 4"):
        T.n_up(work, [pdf], {"per_sheet": 3})


# ------------------------------------------------------------- optimize ---

@needs_gs
def test_compress_preserves_pages(work, pdf):
    r = T.compress(work, [pdf], {"level": "recommended"})
    assert pages_of(r.path) == 3


@needs_gs
def test_compress_never_returns_a_larger_file(work, pdf):
    r = T.compress(work, [pdf], {"level": "extreme"})
    assert r.path.stat().st_size <= pdf.stat().st_size


@needs_gs
def test_grayscale_keeps_text(work, pdf):
    r = T.grayscale(work, [pdf], {})
    assert "The quick brown fox." in text_of(r.path)


@needs_qpdf
def test_repair_recovers_a_broken_xref(work, damaged_pdf):
    """Regression: qpdf exits 3 on successful recovery.

    Treating rc=3 as failure made repair reject exactly the files it exists
    to fix. This test fails if that ever comes back.
    """
    r = T.repair(work, [damaged_pdf], {})
    assert pages_of(r.path) == 3


@needs_qpdf
def test_repair_does_not_inflate(work, damaged_pdf):
    """Regression: --qdf emitted the uncompressed debug form."""
    r = T.repair(work, [damaged_pdf], {})
    assert r.path.stat().st_size < damaged_pdf.stat().st_size * 1.5


# -------------------------------------------------------------- convert ---

def test_pdf_to_jpg_one_page_returns_an_image(work, pdf):
    r = T.pdf_to_jpg(work, [pdf], {"pages": "1", "dpi": 72})
    assert r.media_type == "image/jpeg"


def test_pdf_to_jpg_many_pages_returns_a_zip(work, pdf):
    r = T.pdf_to_jpg(work, [pdf], {"dpi": 72})
    with zipfile.ZipFile(r.path) as z:
        assert len(z.namelist()) == 3


def test_jpg_to_pdf_makes_one_page_per_image(work, jpg):
    r = T.jpg_to_pdf(work, [jpg, jpg], {"size": "a4"})
    assert pages_of(r.path) == 2


def test_jpg_to_pdf_rejects_a_non_image(work, not_a_pdf):
    with pytest.raises(T.ToolError, match="not a readable image"):
        T.jpg_to_pdf(work, [not_a_pdf], {})


def test_pdf_to_markdown_extracts_text(work, pdf):
    r = T.pdf_to_markdown(work, [pdf], {})
    assert "The quick brown fox." in r.path.read_text()


def test_extract_images_finds_the_embedded_image(work, image_pdf):
    r = T.extract_images(work, [image_pdf], {"min_size": 10})
    assert r.path.exists() and r.path.stat().st_size > 0


def test_extract_images_honours_the_size_floor(work, image_pdf):
    with pytest.raises(T.ToolError, match="no embedded images"):
        T.extract_images(work, [image_pdf], {"min_size": 5000})


def test_extract_attachments_reports_when_there_are_none(work, pdf):
    with pytest.raises(T.ToolError, match="no file attachments"):
        T.extract_attachments(work, [pdf], {})


@needs_office
def test_office_to_pdf_converts(work, tmp_path):
    src = tmp_path / "note.txt"
    src.write_text("Hello from LibreOffice.\n")
    r = T.office_to_pdf(work, [src], {})
    assert "Hello from LibreOffice." in text_of(r.path)


def test_office_to_pdf_rejects_unknown_types(work, pdf):
    with pytest.raises(T.ToolError, match="unsupported type"):
        T.office_to_pdf(work, [pdf], {})


# ----------------------------------------------------------------- edit ---

def test_watermark_adds_the_text(work, pdf):
    r = T.watermark(work, [pdf], {"text": "DRAFT"})
    assert "DRAFT" in text_of(r.path)


def test_watermark_requires_text(work, pdf):
    with pytest.raises(T.ToolError, match="text is required"):
        T.watermark(work, [pdf], {"text": "   "})


def test_watermark_tiles_repeat_per_page(work, pdf):
    r = T.watermark(work, [pdf], {"text": "COPY", "mode": "tile"})
    d = fitz.open(r.path)
    assert d[0].get_text().count("COPY") >= 8
    d.close()


def test_page_numbers_use_the_format(work, pdf):
    r = T.page_numbers(work, [pdf], {"format": "Page {n} of {total}"})
    assert "Page 1 of 3" in text_of(r.path)


def test_page_numbers_reject_a_format_without_the_token(work, pdf):
    with pytest.raises(T.ToolError, match=r"\{n\}"):
        T.page_numbers(work, [pdf], {"format": "no token here"})


def test_page_numbers_honour_the_start_offset(work, pdf):
    r = T.page_numbers(work, [pdf], {"format": "{n}", "start": 10})
    assert "10" in text_of(r.path)


def test_header_footer_expands_tokens(work, pdf):
    r = T.header_footer(work, [pdf], {"text": "{filename} p{n}"})
    assert "sample p1" in text_of(r.path)


def test_crop_shrinks_the_page(work, pdf):
    before = fitz.open(pdf)[0].rect.width
    r = T.crop(work, [pdf], {"left": 50, "right": 50})
    d = fitz.open(r.path)
    assert d[0].rect.width < before
    d.close()


def test_crop_rejects_an_empty_result(work, pdf):
    with pytest.raises(T.ToolError, match="leaves nothing"):
        T.crop(work, [pdf], {"left": 5000})


def test_metadata_sets_the_title(work, pdf):
    r = T.metadata(work, [pdf], {"title": "New Title"})
    d = fitz.open(r.path)
    assert d.metadata["title"] == "New Title"
    d.close()


def test_metadata_strip_clears_everything(work, pdf):
    seeded = T.metadata(work, [pdf], {"title": "Secret", "author": "Someone"})
    r = T.metadata(work, [seeded.path], {"strip": "1"})
    d = fitz.open(r.path)
    assert not d.metadata.get("title") and not d.metadata.get("author")
    d.close()


# ------------------------------------------------------------- security ---

def test_protect_produces_an_encrypted_file(work, pdf):
    r = T.protect(work, [pdf], {"password_new": "hunter2"})
    d = fitz.open(r.path)
    assert d.needs_pass
    d.close()


def test_protect_rejects_a_short_password(work, pdf):
    with pytest.raises(T.ToolError, match="at least 4"):
        T.protect(work, [pdf], {"password_new": "ab"})


def test_unlock_needs_the_right_password(work, encrypted_pdf):
    with pytest.raises(T.ToolError, match="password protected"):
        T.unlock(work, [encrypted_pdf], {"password": "wrong"})


def test_unlock_removes_encryption(work, encrypted_pdf):
    r = T.unlock(work, [encrypted_pdf], {"password": "userpw"})
    d = fitz.open(r.path)
    assert not d.needs_pass
    d.close()


def test_redact_destroys_the_text(work, pdf):
    """The whole point: the term must be GONE, not covered."""
    r = T.redact(work, [pdf], {"terms": "CLASSIFIED-MARKER"})
    assert "CLASSIFIED-MARKER" not in text_of(r.path)
    assert "The quick brown fox." in text_of(r.path)   # nothing else lost


def test_redact_reports_when_nothing_matched(work, pdf):
    with pytest.raises(T.ToolError, match="were not found|none of those terms"):
        T.redact(work, [pdf], {"terms": "NOT-PRESENT-ANYWHERE"})


def test_redact_requires_a_term(work, pdf):
    with pytest.raises(T.ToolError, match="at least one term"):
        T.redact(work, [pdf], {"terms": "  \n "})


def test_compare_reports_differences(work, pdf, pdf2):
    r = T.compare(work, [pdf, pdf2], {})
    assert "Second document." in r.path.read_text()


def test_compare_needs_exactly_two(work, pdf):
    with pytest.raises(T.ToolError, match="exactly 2"):
        T.compare(work, [pdf], {})


def test_rasterise_removes_the_text_layer(work, pdf):
    r = T.rasterise(work, [pdf], {"dpi": 72})
    assert text_of(r.path).strip() == ""
    assert pages_of(r.path) == 3


def test_flatten_preserves_pages(work, pdf):
    r = T.flatten(work, [pdf], {})
    assert pages_of(r.path) == 3


# ------------------------------------------------- encrypted input paths ---

def test_tools_reject_encrypted_input_without_a_password(work, encrypted_pdf):
    """Every tool routes through open_pdf, so one guard covers all of them."""
    with pytest.raises(T.ToolError, match="password protected"):
        T.split(work, [encrypted_pdf], {"pages": "1"})


def test_tools_accept_encrypted_input_with_a_password(work, encrypted_pdf):
    r = T.split(work, [encrypted_pdf], {"pages": "1", "password": "userpw"})
    assert pages_of(r.path) == 1


# ----------------------------------------------------- malformed inputs ---

@pytest.mark.parametrize("fixture", ["empty_file", "not_a_pdf"])
def test_garbage_input_raises_a_clean_error(work, request, fixture):
    """Malformed input must produce ToolError (a 400), never an unhandled
    exception (a 500). A 500 tells the user nothing and pages an operator."""
    bad = request.getfixturevalue(fixture)
    with pytest.raises(T.ToolError):
        T.compress(work, [bad], {}) if shutil.which("gs") else T.split(work, [bad], {})


# ------------------------------------------------------------ regressions ---

def _rotated_pdf(path: Path, rotation: int) -> Path:
    doc = fitz.open()
    page = doc.new_page(width=400, height=800)      # clearly portrait
    page.insert_text((50, 60), "TOP EDGE", fontsize=14)
    page.set_rotation(rotation)
    doc.save(path)
    doc.close()
    return path


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_crop_trims_the_edge_the_user_sees(work, rotation):
    """set_cropbox works in unrotated space; the margins are what the user sees.

    On a /Rotate 90 page the visible top edge is the unrotated LEFT edge, so
    applying the margins verbatim trimmed the wrong two sides.
    """
    src = _rotated_pdf(work / f"rot{rotation}.pdf", rotation)
    before = fitz.open(src)
    vis_h_before = before[0].rect.height
    vis_w_before = before[0].rect.width
    before.close()

    r = T.crop(work, [src], {"top": 100, "bottom": 0, "left": 0, "right": 0})
    after = fitz.open(r.path)
    vis = after[0].rect
    after.close()
    # 100 pt came off the visible height; the visible width is untouched.
    assert abs(vis.height - (vis_h_before - 100)) < 1.5
    assert abs(vis.width - vis_w_before) < 1.5


def test_crop_preserves_the_page_rotation(work):
    src = _rotated_pdf(work / "rot90.pdf", 90)
    r = T.crop(work, [src], {"top": 20})
    d = fitz.open(r.path)
    assert d[0].rotation == 90
    d.close()


def test_zip_dedupes_colliding_basenames(work):
    a = work / "one"
    b = work / "two"
    a.mkdir()
    b.mkdir()
    (a / "invoice.pdf").write_bytes(b"A")
    (b / "invoice.pdf").write_bytes(b"B")
    dest = T.zip_dir([a / "invoice.pdf", b / "invoice.pdf"], work / "out.zip")
    with zipfile.ZipFile(dest) as z:
        names = z.namelist()
    assert len(names) == 2 and len(set(names)) == 2


def test_pdf_to_png_does_not_pass_a_null_quality(work, pdf):
    """jpg_quality=None reached the C layer, which wants an int."""
    r = T.pdf_to_jpg(work, [pdf], {"format": "png", "pages": "1"})
    assert r.path.suffix == ".png" and r.path.stat().st_size > 0


def test_page_numbers_total_is_the_document_not_the_selection(work, pdf):
    r = T.page_numbers(work, [pdf], {"format": "{n} of {total}", "pages": "1-2"})
    assert "of 3" in text_of(r.path)
    assert "of 2" not in text_of(r.path)


def test_remove_pages_asks_for_a_selection(work, pdf):
    with pytest.raises(T.ToolError, match="name the pages"):
        T.remove_pages(work, [pdf], {"pages": ""})


def test_subprocess_limits_avoid_preexec_when_prlimit_exists(monkeypatch):
    """preexec_fn is unsafe from a threaded parent; prefer prlimit where present."""
    monkeypatch.setattr(T.shutil, "which", lambda b: "/usr/bin/prlimit")
    argv, preexec = T._wrap_limits(["gs", "-dBATCH"])
    assert preexec is None
    assert argv[0].endswith("prlimit") and "--" in argv
    assert argv[argv.index("--") + 1:] == ["gs", "-dBATCH"]


def test_subprocess_limits_fall_back_to_preexec_without_prlimit(monkeypatch):
    monkeypatch.setattr(T.shutil, "which", lambda b: None)
    argv, preexec = T._wrap_limits(["gs", "-dBATCH"])
    assert argv == ["gs", "-dBATCH"] and preexec is T._limits


# ------------------------------------------------- merge output naming ---

def test_merge_default_name_two_files():
    from pathlib import Path
    ins = [Path("/tmp/report.pdf"), Path("/tmp/invoice.pdf")]
    assert T.merge_default_name(ins) == "report+invoice"


def test_merge_default_name_many_files():
    from pathlib import Path
    ins = [Path(f"/tmp/f{i}.pdf") for i in range(4)]
    assert T.merge_default_name(ins) == "f0+3-more"


def test_output_pdf_name_adds_extension_and_defaults():
    assert T.output_pdf_name("", "fallback") == "fallback.pdf"
    assert T.output_pdf_name("My Report", "fb") == "My Report.pdf"
    assert T.output_pdf_name("thing.PDF", "fb") == "thing.pdf"        # no double ext


def test_output_pdf_name_strips_traversal():
    for bad in ["../../etc/passwd", "..\\win\\sys", "/abs/x"]:
        out = T.output_pdf_name(bad, "fb")
        assert "/" not in out and "\\" not in out and ".." not in out
        assert out.endswith(".pdf")


def test_merge_uses_custom_output_name(work, pdf, pdf2):
    r = T.merge(work, [pdf, pdf2], {"output_name": "combined report"})
    assert r.filename == "combined report.pdf"
    assert r.path.name == "combined report.pdf"


def test_merge_falls_back_to_smart_default(work, pdf, pdf2):
    r = T.merge(work, [pdf, pdf2], {})
    # fixtures are sample.pdf + second.pdf -> "sample+second.pdf"
    assert r.filename == "sample+second.pdf"
