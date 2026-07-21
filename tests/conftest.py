"""Fixtures for the Squish test suite.

Every fixture PDF is GENERATED, not committed. Binary fixtures rot: nobody
can review a diff on them, and a corrupt one produces a failure that looks
like a code bug. Generating them means the preconditions of each test are
readable Python.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import fitz  # noqa: E402


def _text_pdf(path: Path, pages: int = 3, body: str = "") -> Path:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 100), f"Page {i + 1} of {pages}", fontsize=18)
        page.insert_text((72, 140), body or "The quick brown fox.", fontsize=12)
        # A distinct term on one page only, so redaction and search tests can
        # assert on scope rather than just on "something changed".
        if i == 1:
            page.insert_text((72, 180), "CLASSIFIED-MARKER", fontsize=12)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def work(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    return _text_pdf(tmp_path / "sample.pdf")


@pytest.fixture
def pdf2(tmp_path: Path) -> Path:
    return _text_pdf(tmp_path / "second.pdf", pages=2, body="Second document.")


@pytest.fixture
def big_pdf(tmp_path: Path) -> Path:
    """Enough pages to trip the render budget at high DPI."""
    return _text_pdf(tmp_path / "big.pdf", pages=400)


@pytest.fixture
def encrypted_pdf(tmp_path: Path) -> Path:
    src = _text_pdf(tmp_path / "plain.pdf")
    doc = fitz.open(src)
    out = tmp_path / "encrypted.pdf"
    doc.save(out, encryption=fitz.PDF_ENCRYPT_AES_256,
             owner_pw="ownerpw", user_pw="userpw")
    doc.close()
    return out


@pytest.fixture
def damaged_pdf(tmp_path: Path) -> Path:
    """Valid content, deliberately broken cross-reference offset."""
    src = _text_pdf(tmp_path / "ok.pdf")
    data = src.read_bytes()
    i = data.rfind(b"startxref")
    out = tmp_path / "damaged.pdf"
    out.write_bytes(data[:i] + b"startxref\n999999\n%%EOF\n")
    return out


@pytest.fixture
def image_pdf(tmp_path: Path) -> Path:
    """Carries one embedded image, for the extraction tests."""
    doc = fitz.open()
    page = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 300, 200))
    pix.set_rect(pix.irect, (200, 60, 40))
    page.insert_image(fitz.Rect(72, 72, 372, 272), pixmap=pix)
    out = tmp_path / "with_image.pdf"
    doc.save(out)
    doc.close()
    return out


@pytest.fixture
def bookmarked_pdf(tmp_path: Path) -> Path:
    src = _text_pdf(tmp_path / "toc_src.pdf", pages=6)
    doc = fitz.open(src)
    doc.set_toc([[1, "Introduction", 1], [1, "Methods", 3], [1, "Results", 5]])
    out = tmp_path / "bookmarked.pdf"
    doc.save(out)
    doc.close()
    return out


@pytest.fixture
def jpg(tmp_path: Path) -> Path:
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 400, 300))
    pix.set_rect(pix.irect, (30, 90, 200))
    out = tmp_path / "image.jpg"
    pix.save(out)
    return out


@pytest.fixture
def empty_file(tmp_path: Path) -> Path:
    out = tmp_path / "empty.pdf"
    out.write_bytes(b"")
    return out


@pytest.fixture
def not_a_pdf(tmp_path: Path) -> Path:
    out = tmp_path / "lies.pdf"
    out.write_bytes(b"this is plainly not a PDF at all\n" * 50)
    return out
