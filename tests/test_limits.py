"""Resource limits, page parsing, and the registry contract.

These are the guards that stop a small hostile input producing an enormous
output. They matter more than any single tool: /tmp is a tmpfs in both
deployments, so uncontrolled output is uncontrolled memory.
"""

from __future__ import annotations

import fitz
import pytest

import tools as T


# ------------------------------------------------------- page selection ---

@pytest.mark.parametrize("spec,total,want", [
    ("", 5, [0, 1, 2, 3, 4]),          # blank means all
    ("1", 5, [0]),
    ("2-4", 5, [1, 2, 3]),
    ("3,1,2", 5, [2, 0, 1]),           # order is preserved, not sorted
    ("4-", 5, [3, 4]),                 # open-ended right
    ("-2", 5, [0, 1]),                 # open-ended left
    ("1-3,2,3", 5, [0, 1, 2]),         # duplicates collapse
])
def test_parse_pages_accepts(spec, total, want):
    assert T.parse_pages(spec, total) == want


@pytest.mark.parametrize("spec", ["0", "0-2", "1-99", "abc", "5-2", "1--2", "-"])
def test_parse_pages_rejects(spec):
    with pytest.raises(T.ToolError):
        T.parse_pages(spec, 5)


# -------------------------------------------------------------- colours ---

@pytest.mark.parametrize("value,want", [
    ("#000000", (0.0, 0.0, 0.0)),
    ("#ffffff", (1.0, 1.0, 1.0)),
    ("#fff", (1.0, 1.0, 1.0)),
])
def test_hex_rgb_accepts(value, want):
    assert T._hex_rgb(value) == want


@pytest.mark.parametrize("value", ["#12", "zzzzzz", "#gggggg", "", "#1234567"])
def test_hex_rgb_rejects(value):
    with pytest.raises(T.ToolError):
        T._hex_rgb(value)


# ------------------------------------------------------- render budget ---

def test_clamp_dpi_leaves_small_jobs_alone(pdf):
    doc = fitz.open(pdf)
    assert T.clamp_dpi([0, 1, 2], doc, 150) == 150
    doc.close()


def test_clamp_dpi_reduces_oversized_jobs(big_pdf):
    """400 A4 pages at 600 DPI is ~130 gigapixels. It must come down."""
    doc = fitz.open(big_pdf)
    got = T.clamp_dpi(list(range(doc.page_count)), doc, 600)
    doc.close()
    assert got < 600
    assert got >= T.MIN_DPI


def test_clamp_dpi_refuses_the_impossible(big_pdf, monkeypatch):
    monkeypatch.setattr(T, "MAX_RENDER_MP", 0.01)
    doc = fitz.open(big_pdf)
    with pytest.raises(T.ToolError, match="even at"):
        T.clamp_dpi(list(range(doc.page_count)), doc, 300)
    doc.close()


def test_render_tools_respect_the_budget(work, big_pdf, monkeypatch):
    """End-to-end: the clamp is actually wired into pdf_to_jpg, not just
    defined. A guard nobody calls is not a guard."""
    monkeypatch.setattr(T, "MAX_RENDER_MP", 5.0)
    r = T.pdf_to_jpg(work, [big_pdf], {"dpi": 600, "pages": "1-20"})
    assert r.path.exists()


def test_page_count_ceiling_is_enforced(work, pdf, monkeypatch):
    monkeypatch.setattr(T, "MAX_PAGES", 2)
    with pytest.raises(T.ToolError, match="the limit is 2"):
        T.open_pdf(pdf)


# ------------------------------------------------------------- registry ---

def test_tool_keys_are_unique():
    keys = [t.key for t in T.TOOLS]
    assert len(keys) == len(set(keys))


def test_every_tool_is_callable():
    assert all(callable(t.fn) for t in T.TOOLS)


def test_fields_are_well_formed():
    valid = {"text", "number", "select", "checkbox", "color", "password", "textarea"}
    for t in T.TOOLS:
        for f in t.fields or []:
            assert {"name", "kind", "label"} <= set(f), (t.key, f)
            assert f["kind"] in valid, (t.key, f["kind"])


def test_select_defaults_exist_in_their_options():
    """A default outside the option list renders as a blank dropdown, which
    then posts an empty value and fails server-side for no visible reason."""
    for t in T.TOOLS:
        for f in t.fields or []:
            if f["kind"] == "select":
                allowed = [str(o[0]) for o in f["options"]]
                assert str(f.get("default")) in allowed, (t.key, f["name"])


def test_multi_file_tools_declare_a_sane_minimum():
    for t in T.TOOLS:
        if t.min_files > 1:
            assert t.multi, f"{t.key} needs several files but is not multi"


# ------------------------------------------------------------ filenames ---

@pytest.mark.parametrize("given", [
    "../../etc/passwd",
    "..\\..\\windows\\system32\\config",
    "/absolute/path.pdf",
    "name\x00truncated.pdf",
])
def test_safe_component_strips_traversal(given):
    out = T.safe_component(given)
    assert "/" not in out and "\\" not in out and ".." not in out
