"""Squish PDF tool implementations.

Every tool is a pure function over a temp working directory:

    fn(work: Path, inputs: list[Path], p: dict) -> Result

It writes its output inside `work` and returns a Result describing the file to
stream back. Nothing persists: app.py deletes `work` after the response is sent.

Heavy lifting is delegated to battle-tested engines rather than reimplemented:
  PyMuPDF      page surgery, rendering, redaction, overlays
  Ghostscript  compression, PDF/A
  LibreOffice  Office <-> PDF
  ocrmypdf     OCR (wraps tesseract + ghostscript)
  pdf2docx     PDF -> Word with layout reconstruction
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import fitz  # PyMuPDF

log = logging.getLogger("uvicorn.error")

# ------------------------------------------------------------- budgets ---
# MAX_UPLOAD_MB caps what comes IN. These cap what goes OUT, which is the
# dimension that actually causes trouble: a 40,000-page PDF is small on disk
# but rasterises to tens of gigabytes, and /tmp is a tmpfs (RAM) in both the
# compose and Kubernetes deployments.
MAX_PAGES = int(os.environ.get("MAX_PAGES", "5000"))
# Total pixels a single job may render, in megapixels. 4000 MP is roughly
# 250 A4 pages at 150 DPI, or 1000 pages at 75 DPI after clamping.
MAX_RENDER_MP = float(os.environ.get("MAX_RENDER_MP", "4000"))
MIN_DPI = 36

PDF = "application/pdf"
ZIP = "application/zip"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


class ToolError(Exception):
    """Raised for user-fixable problems; surfaced as HTTP 400."""


@dataclass
class Result:
    path: Path
    media_type: str
    filename: str


# ---------------------------------------------------------------- helpers ---

def run(cmd: list[str], cwd: Path | None = None, timeout: int = 300,
        ok_codes: tuple[int, ...] = (0,)) -> None:
    """Run a subprocess from an exec array (never a shell string).

    Subprocess stderr is captured into the uvicorn logger rather than
    discarded -- silent engine failures are undebuggable in production.

    `ok_codes` exists because qpdf exits 3 when it *successfully* recovered a
    damaged file. Treating that as failure would make the repair tool reject
    exactly the documents it is meant to fix.
    """
    env = dict(os.environ)
    # LibreOffice and Ghostscript both need a writable HOME. Under a read-only
    # root filesystem on Kubernetes only /tmp is writable, so point HOME there.
    env.setdefault("HOME", str(cwd or Path("/tmp")))
    # Keep the real binary name for messages: the exec array may be wrapped in
    # prlimit, and "prlimit failed" tells the user nothing.
    binary = cmd[0]
    argv, preexec = _wrap_limits(cmd)
    log.info("exec: %s", " ".join(cmd[:4]) + (" ..." if len(cmd) > 4 else ""))
    try:
        proc = subprocess.run(
            argv, cwd=cwd, env=env, timeout=timeout,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            preexec_fn=preexec,
        )
    except subprocess.TimeoutExpired:
        raise ToolError(f"{binary} timed out after {timeout}s")
    except FileNotFoundError:
        raise ToolError(f"{binary} is not installed in this image")
    if proc.returncode not in ok_codes:
        err = proc.stderr.decode("utf-8", "replace").strip()
        log.error("%s failed rc=%s: %s", binary, proc.returncode, err[-2000:])
        # Quote the shortest decisive line back to the user, not the whole dump.
        last = err.splitlines()[-1] if err else "no stderr"
        raise ToolError(f"{binary} failed: {last[:300]}")


# Ceilings applied to engine child processes. Ghostscript in particular has a
# long history of memory-exhaustion bugs on malformed input; a hostile PDF
# should kill its own subprocess, not the pod. Set to 0 to disable.
SUBPROC_MEM_MB = int(os.environ.get("SUBPROC_MEM_MB", "1536"))
SUBPROC_CPU_SEC = int(os.environ.get("SUBPROC_CPU_SEC", "600"))


def _wrap_limits(cmd: list[str]) -> tuple[list[str], Callable[[], None] | None]:
    """Apply the child resource ceilings by the safest means available.

    Tools run inside asyncio.to_thread, so the process forking these children
    is multi-threaded, and CPython documents preexec_fn as unsafe there: the
    child runs arbitrary Python between fork and exec while it may hold a lock
    another thread was using, which can deadlock the child forever.

    util-linux `prlimit` sets the same RLIMITs from outside, with no code
    running in the forked child at all -- so prefer it wherever it exists,
    which is every Linux image this ships in. macOS has no prlimit, and native
    `run-local.sh` is the case that matters there, so keep preexec_fn as the
    fallback: a small deadlock risk beats no memory ceiling at all.

    Windows has neither prlimit nor preexec_fn -- passing preexec_fn on Windows
    raises ValueError -- and the `resource` module does not exist there, so the
    limits are simply unavailable. Return None; the engine still runs, just
    without an in-process memory ceiling (Docker is the recommended Windows
    path precisely because the container can cap memory instead).
    """
    if SUBPROC_MEM_MB <= 0 and SUBPROC_CPU_SEC <= 0:
        return cmd, None
    prlimit = shutil.which("prlimit")
    if prlimit:
        wrapped = [prlimit]
        if SUBPROC_MEM_MB > 0:
            wrapped.append(f"--as={SUBPROC_MEM_MB * 1024 * 1024}")
        if SUBPROC_CPU_SEC > 0:
            wrapped.append(f"--cpu={SUBPROC_CPU_SEC}")
        wrapped.append("--core=0")
        # `--` keeps prlimit from eating flags that belong to the real command.
        return wrapped + ["--"] + cmd, None
    if os.name != "posix":          # Windows: no prlimit, no preexec_fn
        return cmd, None
    return cmd, _limits


def _limits() -> None:
    """Fallback path only (no prlimit). Applied in the child between fork and exec.

    RLIMIT_AS bounds address space and RLIMIT_CPU bounds CPU seconds, so a
    runaway engine dies on its own rather than being reaped by the OOM killer
    -- which on Kubernetes takes the whole container with it, including the
    other requests that pod was serving.
    """
    try:
        import resource
        if SUBPROC_MEM_MB > 0:
            b = SUBPROC_MEM_MB * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (b, b))
        if SUBPROC_CPU_SEC > 0:
            resource.setrlimit(resource.RLIMIT_CPU,
                               (SUBPROC_CPU_SEC, SUBPROC_CPU_SEC))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except Exception:      # non-Linux, or limits already lower than requested
        pass


def open_pdf(path: Path, password: str = "") -> fitz.Document:
    try:
        doc = fitz.open(path)
    except Exception as exc:
        raise ToolError(f"cannot read {path.name}: {exc}")
    if doc.needs_pass:
        if not password or not doc.authenticate(password):
            doc.close()
            raise ToolError(f"{path.name} is password protected -- supply the password")
    if doc.page_count > MAX_PAGES:
        n = doc.page_count
        doc.close()
        raise ToolError(f"{path.name} has {n} pages; the limit is {MAX_PAGES}")
    return doc


def clamp_dpi(pages: list[int], doc: fitz.Document, dpi: int) -> int:
    """Reduce DPI until the whole job fits the render budget.

    Rendering is the one operation where a small input produces an unbounded
    output: an A4 page at 400 DPI is a 3308x4676 pixmap, about 46 MB
    uncompressed, and nothing about the input file size predicts the page
    count. Rather than refusing the job outright, scale the resolution down so
    it completes, and say so in the log.
    """
    if not pages:
        return dpi
    # Sum actual page areas: a document of A0 posters costs far more than A4.
    sq_inches = 0.0
    for i in pages:
        r = doc[i].rect
        sq_inches += (r.width / 72.0) * (r.height / 72.0)
    budget_px = MAX_RENDER_MP * 1_000_000
    wanted_px = sq_inches * dpi * dpi
    if wanted_px <= budget_px:
        return dpi
    safe = int((budget_px / sq_inches) ** 0.5)
    if safe < MIN_DPI:
        raise ToolError(
            f"rendering {len(pages)} pages exceeds the limit even at {MIN_DPI} DPI "
            f"-- select fewer pages"
        )
    log.warning("render budget: clamping %s DPI to %s for %d pages", dpi, safe, len(pages))
    return safe


def parse_pages(spec: str, total: int) -> list[int]:
    """'1-3,5,8-' -> zero-based page indices, deduped, in the order written.

    Empty spec means every page. Raises on out-of-range so the user gets a
    clear error instead of a silently truncated document.
    """
    spec = (spec or "").strip()
    if not spec:
        return list(range(total))
    out: list[int] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.fullmatch(r"(\d+)?\s*-\s*(\d+)?", chunk)
        if m:
            # A dash with a number on at least one side is a real range. A bare
            # "-" carries no bound and is almost always a typo, not "everything";
            # rejecting it beats silently selecting the whole document.
            if not m.group(1) and not m.group(2):
                raise ToolError(f"bad page range: {chunk!r}")
            start = int(m.group(1)) if m.group(1) else 1
            end = int(m.group(2)) if m.group(2) else total
        elif chunk.isdigit():
            start = end = int(chunk)
        else:
            raise ToolError(f"bad page range: {chunk!r}")
        if start < 1 or end > total or start > end:
            raise ToolError(f"page range {chunk!r} outside document (1-{total})")
        out.extend(range(start - 1, end))
    seen, uniq = set(), []
    for i in out:
        if i not in seen:
            seen.add(i)
            uniq.append(i)
    return uniq


def zip_dir(files: list[Path], dest: Path) -> Path:
    """Zip by basename, de-duplicating collisions.

    Two attachments both called `invoice.pdf` used to write the same archive
    name twice: most extractors keep only the last, so the user silently lost a
    file. Suffix repeats instead.
    """
    used: set[str] = set()
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(files):
            name = f.name
            if name in used:
                i = 2
                while f"{f.stem}({i}){f.suffix}" in used:
                    i += 1
                name = f"{f.stem}({i}){f.suffix}"
            used.add(name)
            z.write(f, name)
    return dest


def stem(path: Path) -> str:
    return re.sub(r"[^\w.\- ]+", "_", path.stem)[:80] or "document"


def save(doc: fitz.Document, out: Path, shrink: bool = True) -> Path:
    """Save with the flags that actually matter for size.

    garbage=4 runs full cross-reference garbage collection and merges
    duplicate objects; deflate recompresses streams. Without these, a merge of
    ten files that share a font embeds that font ten times.
    """
    doc.save(out, garbage=4 if shrink else 0, deflate=True, clean=shrink)
    return out


# ------------------------------------------------------------- organize ---

def merge(work: Path, inputs: list[Path], p: dict) -> Result:
    if len(inputs) < 2:
        raise ToolError("merge needs at least 2 files")
    out = fitz.open()
    for f in inputs:
        src = open_pdf(f, p.get("password", ""))
        out.insert_pdf(src)
        src.close()
    dest = save(out, work / "merged.pdf")
    out.close()
    return Result(dest, PDF, "merged.pdf")


def split(work: Path, inputs: list[Path], p: dict) -> Result:
    """mode=ranges  -> one PDF containing the selected pages
       mode=every   -> one PDF per page, zipped
       mode=chunks  -> fixed-size groups of pages, zipped
    """
    src = open_pdf(inputs[0], p.get("password", ""))
    total = src.page_count
    base = stem(inputs[0])
    mode = p.get("mode", "ranges")

    if mode == "ranges":
        pages = parse_pages(p.get("pages", ""), total)
        out = fitz.open()
        for i in pages:
            out.insert_pdf(src, from_page=i, to_page=i)
        dest = save(out, work / f"{base}_pages.pdf")
        out.close()
        src.close()
        return Result(dest, PDF, f"{base}_pages.pdf")

    size = 1 if mode == "every" else max(1, int(p.get("size", 1) or 1))
    parts: list[Path] = []
    for start in range(0, total, size):
        end = min(start + size - 1, total - 1)
        out = fitz.open()
        out.insert_pdf(src, from_page=start, to_page=end)
        label = f"{start + 1}" if start == end else f"{start + 1}-{end + 1}"
        part = save(out, work / f"{base}_{label}.pdf")
        out.close()
        parts.append(part)
    src.close()
    dest = zip_dir(parts, work / f"{base}_split.zip")
    return Result(dest, ZIP, f"{base}_split.zip")


def remove_pages(work: Path, inputs: list[Path], p: dict) -> Result:
    src = open_pdf(inputs[0], p.get("password", ""))
    # A blank spec means "every page" everywhere else, which here would mean
    # deleting the whole document. Ask instead of reporting it as an error
    # about removing every page, which reads like a bug.
    if not (p.get("pages") or "").strip():
        src.close()
        raise ToolError("name the pages to remove, e.g. 2 or 4-6")
    drop = set(parse_pages(p.get("pages", ""), src.page_count))
    if not drop:
        raise ToolError("select at least one page to remove")
    keep = [i for i in range(src.page_count) if i not in drop]
    if not keep:
        raise ToolError("that would remove every page")
    out = fitz.open()
    for i in keep:
        out.insert_pdf(src, from_page=i, to_page=i)
    base = stem(inputs[0])
    dest = save(out, work / f"{base}_trimmed.pdf")
    out.close()
    src.close()
    return Result(dest, PDF, f"{base}_trimmed.pdf")


def organize(work: Path, inputs: list[Path], p: dict) -> Result:
    """Reorder pages to an explicit sequence, e.g. '3,1,2,4-'."""
    src = open_pdf(inputs[0], p.get("password", ""))
    order = parse_pages(p.get("pages", ""), src.page_count)
    out = fitz.open()
    for i in order:
        out.insert_pdf(src, from_page=i, to_page=i)
    base = stem(inputs[0])
    dest = save(out, work / f"{base}_organized.pdf")
    out.close()
    src.close()
    return Result(dest, PDF, f"{base}_organized.pdf")


def rotate(work: Path, inputs: list[Path], p: dict) -> Result:
    src = open_pdf(inputs[0], p.get("password", ""))
    angle = int(p.get("angle", 90))
    if angle % 90:
        raise ToolError("angle must be a multiple of 90")
    targets = set(parse_pages(p.get("pages", ""), src.page_count))
    for i in targets:
        page = src[i]
        page.set_rotation((page.rotation + angle) % 360)
    base = stem(inputs[0])
    dest = save(src, work / f"{base}_rotated.pdf")
    src.close()
    return Result(dest, PDF, f"{base}_rotated.pdf")


# ------------------------------------------------------------- optimize ---

# Ghostscript's presets, cheapest to best. /screen downsamples images to 72dpi,
# /ebook to 150dpi, /printer to 300dpi.
GS_LEVELS = {"extreme": "/screen", "recommended": "/ebook", "low": "/printer"}


def compress(work: Path, inputs: list[Path], p: dict) -> Result:
    if not inputs[0].stat().st_size:
        raise ToolError(f"cannot read {inputs[0].name}: file is empty")
    level = GS_LEVELS.get(p.get("level", "recommended"), "/ebook")
    base = stem(inputs[0])
    dest = work / f"{base}_compressed.pdf"
    run([
        "gs", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.7",
        f"-dPDFSETTINGS={level}", "-dNOPAUSE", "-dQUIET", "-dBATCH",
        "-dDetectDuplicateImages=true", "-dCompressFonts=true",
        f"-sOutputFile={dest}", str(inputs[0]),
    ], cwd=work)
    # Ghostscript can inflate an already-optimised file. Keep the smaller one
    # rather than handing back a "compressed" file that is bigger.
    if dest.stat().st_size >= inputs[0].stat().st_size:
        log.info("gs output larger than input; returning original")
        shutil.copy(inputs[0], dest)
    return Result(dest, PDF, f"{base}_compressed.pdf")


def repair(work: Path, inputs: list[Path], p: dict) -> Result:
    """Rebuild a damaged cross-reference table.

    qpdf reconstructs the xref by scanning for object headers, recovering far
    more than a plain reparse. Two details matter:
      * rc 3 means "recovered, with warnings" -- the success case here.
      * do NOT pass --qdf: that emits the uncompressed debugging form, which
        roughly doubles file size for no benefit to the user.
    """
    base = stem(inputs[0])
    dest = work / f"{base}_repaired.pdf"
    try:
        run(["qpdf", "--object-streams=generate", "--stream-data=compress",
             str(inputs[0]), str(dest)], cwd=work, ok_codes=(0, 3))
    except ToolError as exc:
        log.warning("qpdf repair failed (%s), falling back to PyMuPDF", exc)
        src = fitz.open(inputs[0])   # fitz repairs on open where it can
        save(src, dest)
        src.close()
    if not dest.exists() or dest.stat().st_size == 0:
        raise ToolError("the file is too damaged to recover")
    return Result(dest, PDF, f"{base}_repaired.pdf")


def ocr(work: Path, inputs: list[Path], p: dict) -> Result:
    """Make a scanned PDF searchable. Text layer is added, image is kept."""
    base = stem(inputs[0])
    dest = work / f"{base}_ocr.pdf"
    lang = re.sub(r"[^a-z+]", "", str(p.get("lang", "eng")).lower()) or "eng"
    cmd = ["ocrmypdf", "--output-type", "pdf", "-l", lang, "--jobs", "2"]
    if p.get("force"):
        # Re-OCR pages that already carry a text layer.
        cmd.append("--force-ocr")
    else:
        # Default: skip pages that already have text instead of erroring out.
        cmd.append("--skip-text")
    if p.get("deskew"):
        cmd.append("--deskew")
    run(cmd + [str(inputs[0]), str(dest)], cwd=work, timeout=900)
    return Result(dest, PDF, f"{base}_ocr.pdf")


# -------------------------------------------------------------- convert ---

def pdf_to_jpg(work: Path, inputs: list[Path], p: dict) -> Result:
    src = open_pdf(inputs[0], p.get("password", ""))
    dpi = max(MIN_DPI, min(600, int(p.get("dpi", 150))))
    fmt = "png" if p.get("format") == "png" else "jpg"
    base = stem(inputs[0])
    pages = parse_pages(p.get("pages", ""), src.page_count)
    dpi = clamp_dpi(pages, src, dpi)
    # jpg_quality must be omitted for PNG, not passed as None: PyMuPDF hands the
    # value straight to the C layer, which wants an int.
    opts: dict[str, Any] = {}
    if fmt == "jpg":
        opts["jpg_quality"] = max(1, min(100, int(p.get("quality", 85))))
    files: list[Path] = []
    for i in pages:
        pix = src[i].get_pixmap(dpi=dpi)
        f = work / f"{base}_{i + 1:04d}.{fmt}"
        pix.save(f, **opts)
        files.append(f)
    src.close()
    if not files:
        raise ToolError("no pages selected")
    if len(files) == 1:
        mt = "image/png" if fmt == "png" else "image/jpeg"
        return Result(files[0], mt, files[0].name)
    dest = zip_dir(files, work / f"{base}_images.zip")
    return Result(dest, ZIP, f"{base}_images.zip")


def jpg_to_pdf(work: Path, inputs: list[Path], p: dict) -> Result:
    """One image per page. Images are fitted inside the page, not stretched."""
    sizes = {
        "fit": None,
        "a4": fitz.paper_rect("a4"),
        "letter": fitz.paper_rect("letter"),
    }
    target = sizes.get(p.get("size", "fit"), None)
    margin = max(0, min(200, int(p.get("margin", 0))))
    out = fitz.open()
    for f in inputs:
        try:
            img = fitz.open(f)
            rect = img[0].rect
            pdf_bytes = img.convert_to_pdf()
            img.close()
        except Exception as exc:
            raise ToolError(f"{f.name} is not a readable image: {exc}")
        src = fitz.open("pdf", pdf_bytes)
        if target is None:
            page = out.new_page(width=rect.width, height=rect.height)
            box = page.rect
        else:
            page = out.new_page(width=target.width, height=target.height)
            box = page.rect + (margin, margin, -margin, -margin)
            # Preserve aspect ratio inside the margin box.
            scale = min(box.width / rect.width, box.height / rect.height)
            w, h = rect.width * scale, rect.height * scale
            cx, cy = box.x0 + box.width / 2, box.y0 + box.height / 2
            box = fitz.Rect(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
        page.show_pdf_page(box, src, 0)
        src.close()
    if not out.page_count:
        raise ToolError("no images supplied")
    dest = save(out, work / "images.pdf")
    out.close()
    return Result(dest, PDF, "images.pdf")


OFFICE_EXT = {".doc", ".docx", ".odt", ".rtf", ".txt",
              ".xls", ".xlsx", ".ods", ".csv",
              ".ppt", ".pptx", ".odp"}


LO_TEMPLATE = Path(os.environ.get("LO_PROFILE_TEMPLATE", "/opt/lo-profile"))


def office_to_pdf(work: Path, inputs: list[Path], p: dict) -> Result:
    """Word/Excel/PowerPoint -> PDF via headless LibreOffice.

    Each request gets its OWN profile directory, because concurrent soffice
    processes sharing one profile corrupt it. Building a profile from scratch
    costs 3-5 seconds, so the image bakes a warm one at /opt/lo-profile and we
    copy that instead -- a few MB of file copy versus a full LibreOffice
    first-run. Falls back to cold creation if the template is absent (native
    `run-local.sh`, or a custom image).
    """
    outs: list[Path] = []
    profile = work / "lo_profile"
    if LO_TEMPLATE.is_dir():
        try:
            shutil.copytree(LO_TEMPLATE, profile)
        except Exception as exc:
            log.warning("could not seed LibreOffice profile: %s", exc)
    for f in inputs:
        if f.suffix.lower() not in OFFICE_EXT:
            raise ToolError(f"{f.name}: unsupported type {f.suffix}")
        run([
            "soffice", "--headless", "--norestore", "--nolockcheck",
            f"-env:UserInstallation=file://{profile}",
            "--convert-to", "pdf", "--outdir", str(work), str(f),
        ], cwd=work, timeout=600)
        produced = work / (f.stem + ".pdf")
        if not produced.exists():
            raise ToolError(f"LibreOffice produced no output for {f.name}")
        outs.append(produced)
    if len(outs) == 1:
        return Result(outs[0], PDF, outs[0].name)
    dest = zip_dir(outs, work / "converted.zip")
    return Result(dest, ZIP, "converted.zip")


def pdf_to_word(work: Path, inputs: list[Path], p: dict) -> Result:
    """Reconstructs paragraphs, tables and images. Complex layouts drift."""
    from pdf2docx import Converter
    base = stem(inputs[0])
    dest = work / f"{base}.docx"
    conv = Converter(str(inputs[0]), password=p.get("password") or None)
    try:
        conv.convert(str(dest), start=0, end=None)
    except Exception as exc:
        raise ToolError(f"conversion failed: {exc}")
    finally:
        conv.close()
    return Result(dest, DOCX, f"{base}.docx")


def pdf_to_excel(work: Path, inputs: list[Path], p: dict) -> Result:
    """Extracts detected tables, one worksheet per table.

    This is honest table extraction, not OCR: a PDF with no ruled or
    whitespace-aligned table structure will yield nothing.
    """
    from openpyxl import Workbook
    src = open_pdf(inputs[0], p.get("password", ""))
    wb = Workbook()
    wb.remove(wb.active)
    found = 0
    for pno in parse_pages(p.get("pages", ""), src.page_count):
        for t, table in enumerate(src[pno].find_tables().tables, 1):
            found += 1
            ws = wb.create_sheet(f"p{pno + 1}_t{t}"[:31])
            for row in table.extract():
                ws.append([("" if c is None else str(c)) for c in row])
    src.close()
    if not found:
        raise ToolError("no tables detected -- run OCR first if this is a scan")
    base = stem(inputs[0])
    dest = work / f"{base}.xlsx"
    wb.save(dest)
    return Result(dest, XLSX, f"{base}.xlsx")


def pdf_to_powerpoint(work: Path, inputs: list[Path], p: dict) -> Result:
    """One slide per page, rendered at full bleed. Faithful, not editable."""
    from pptx import Presentation
    from pptx.util import Emu
    src = open_pdf(inputs[0], p.get("password", ""))
    dpi = max(72, min(300, int(p.get("dpi", 150))))
    pages = parse_pages(p.get("pages", ""), src.page_count)
    dpi = clamp_dpi(pages, src, dpi)
    if not pages:
        src.close()
        raise ToolError("no pages selected")
    prs = Presentation()
    # Size the deck from the FIRST SELECTED page, not page 0 -- page 0 may not
    # even be in the selection. A deck has one slide size, so mixed page sizes
    # still have to be letterboxed rather than stretched.
    first = src[pages[0]].rect
    # PowerPoint measures in EMU; 914400 EMU per inch, PDF points are 1/72 inch.
    prs.slide_width = Emu(int(first.width / 72 * 914400))
    prs.slide_height = Emu(int(first.height / 72 * 914400))
    blank = prs.slide_layouts[6]
    for pno in pages:
        img = work / f"slide_{pno:04d}.png"
        src[pno].get_pixmap(dpi=dpi).save(img)
        slide = prs.slides.add_slide(blank)
        r = src[pno].rect
        # Fit inside the slide, preserving aspect ratio, centred.
        scale = min(prs.slide_width / (r.width / 72 * 914400),
                    prs.slide_height / (r.height / 72 * 914400))
        w = int(r.width / 72 * 914400 * scale)
        h = int(r.height / 72 * 914400 * scale)
        slide.shapes.add_picture(str(img), Emu(int((prs.slide_width - w) / 2)),
                                 Emu(int((prs.slide_height - h) / 2)),
                                 width=Emu(w), height=Emu(h))
    src.close()
    base = stem(inputs[0])
    dest = work / f"{base}.pptx"
    prs.save(dest)
    return Result(dest, PPTX, f"{base}.pptx")


def pdf_to_pdfa(work: Path, inputs: list[Path], p: dict) -> Result:
    """Archival PDF/A-2b. ocrmypdf drives Ghostscript's PDF/A pipeline."""
    base = stem(inputs[0])
    dest = work / f"{base}_pdfa.pdf"
    run(["ocrmypdf", "--output-type", "pdfa-2", "--skip-text",
         "--tesseract-timeout", "0", str(inputs[0]), str(dest)],
        cwd=work, timeout=900)
    return Result(dest, PDF, f"{base}_pdfa.pdf")


def pdf_to_markdown(work: Path, inputs: list[Path], p: dict) -> Result:
    src = open_pdf(inputs[0], p.get("password", ""))
    lines: list[str] = []
    for pno in parse_pages(p.get("pages", ""), src.page_count):
        lines.append(f"\n---\n\n<!-- page {pno + 1} -->\n")
        lines.append(src[pno].get_text("text").strip())
    src.close()
    base = stem(inputs[0])
    dest = work / f"{base}.md"
    dest.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return Result(dest, "text/markdown", f"{base}.md")


# ------------------------------------------------------------------ edit ---

POSITIONS = {
    "top-left": (0.10, 0.08), "top": (0.50, 0.08), "top-right": (0.90, 0.08),
    "left": (0.10, 0.50), "center": (0.50, 0.50), "right": (0.90, 0.50),
    "bottom-left": (0.10, 0.94), "bottom": (0.50, 0.94),
    "bottom-right": (0.90, 0.94),
}


def watermark(work: Path, inputs: list[Path], p: dict) -> Result:
    src = open_pdf(inputs[0], p.get("password", ""))
    text = str(p.get("text") or "").strip()
    if not text:
        raise ToolError("watermark text is required")
    size = max(6, min(200, int(p.get("size", 42))))
    opacity = max(0.05, min(1.0, float(p.get("opacity", 0.25))))
    angle = int(p.get("angle", 45))
    color = _hex_rgb(p.get("color") or "#7c5cff")
    fx, fy = POSITIONS.get(p.get("position", "center"), (0.5, 0.5))
    mode = p.get("mode", "single")  # single | tile

    for pno in parse_pages(p.get("pages", ""), src.page_count):
        page = src[pno]
        r = page.rect
        spots = []
        if mode == "tile":
            step_x, step_y = r.width / 3, r.height / 4
            for gx in range(3):
                for gy in range(4):
                    spots.append((step_x * (gx + 0.5), step_y * (gy + 0.5)))
        else:
            spots.append((r.width * fx, r.height * fy))
        for cx, cy in spots:
            # Morph rotates the text about its own insertion point; without the
            # pivot the glyphs rotate around the page origin and fly off-page.
            pivot = fitz.Point(cx, cy)
            morph = (pivot, fitz.Matrix(1, 1).prerotate(angle))
            page.insert_text(
                pivot - fitz.Point(len(text) * size * 0.25, 0),
                text, fontsize=size, fontname="helv", color=color,
                fill_opacity=opacity, stroke_opacity=opacity, morph=morph,
                overlay=bool(p.get("overlay", True)),
            )
    base = stem(inputs[0])
    dest = save(src, work / f"{base}_watermarked.pdf")
    src.close()
    return Result(dest, PDF, f"{base}_watermarked.pdf")


def page_numbers(work: Path, inputs: list[Path], p: dict) -> Result:
    src = open_pdf(inputs[0], p.get("password", ""))
    start = int(p.get("start", 1))
    size = max(6, min(72, int(p.get("size", 11))))
    fmt = str(p.get("format", "{n}"))
    if "{n}" not in fmt:
        raise ToolError("format must contain {n}")
    fx, fy = POSITIONS.get(p.get("position", "bottom"), (0.5, 0.94))
    color = _hex_rgb(p.get("color") or "#000000")
    pages = parse_pages(p.get("pages", ""), src.page_count)
    # {total} is the document's page count, not the count of pages being
    # stamped -- numbering pages 1-3 of a 10-page file must not say "of 3".
    total = src.page_count
    for seq, pno in enumerate(pages):
        page = src[pno]
        label = fmt.replace("{n}", str(start + seq)).replace("{total}", str(total))
        r = page.rect
        w = fitz.get_text_length(label, fontname="helv", fontsize=size)
        page.insert_text(
            fitz.Point(r.width * fx - w / 2, r.height * fy),
            label, fontsize=size, fontname="helv", color=color, overlay=True,
        )
    base = stem(inputs[0])
    dest = save(src, work / f"{base}_numbered.pdf")
    src.close()
    return Result(dest, PDF, f"{base}_numbered.pdf")


# set_cropbox works in UNROTATED page space, but the user picks margins against
# what they see on screen. On a /Rotate 90 page the visible top edge is the
# unrotated LEFT edge, so applying the margins verbatim trims the wrong sides.
# Map visible edge -> unrotated edge per rotation.
_ROT_EDGES = {
    0:   {"top": "top", "right": "right", "bottom": "bottom", "left": "left"},
    90:  {"top": "left", "right": "top", "bottom": "right", "left": "bottom"},
    180: {"top": "bottom", "right": "left", "bottom": "top", "left": "right"},
    270: {"top": "right", "right": "bottom", "bottom": "left", "left": "top"},
}


def crop(work: Path, inputs: list[Path], p: dict) -> Result:
    """Trim by margin in points from each edge, as the page is displayed."""
    src = open_pdf(inputs[0], p.get("password", ""))
    m = {k: float(p.get(k, 0) or 0) for k in ("top", "bottom", "left", "right")}
    for pno in parse_pages(p.get("pages", ""), src.page_count):
        page = src[pno]
        rot = page.rotation % 360
        mapping = _ROT_EDGES.get(rot)
        if mapping is None:                     # non-multiple-of-90 rotation
            raise ToolError(f"page {pno + 1} has an unsupported rotation ({rot})")
        # Un-rotate to get the coordinate space set_cropbox expects, then put
        # the rotation back afterwards so the page still displays as before.
        page.set_rotation(0)
        r = page.rect
        um = {unrot: m[vis] for vis, unrot in mapping.items()}
        box = fitz.Rect(r.x0 + um["left"], r.y0 + um["top"],
                        r.x1 - um["right"], r.y1 - um["bottom"])
        if box.width <= 1 or box.height <= 1:
            page.set_rotation(rot)
            raise ToolError(f"crop leaves nothing on page {pno + 1}")
        page.set_cropbox(box)
        page.set_rotation(rot)
    base = stem(inputs[0])
    dest = save(src, work / f"{base}_cropped.pdf")
    src.close()
    return Result(dest, PDF, f"{base}_cropped.pdf")


# -------------------------------------------------------------- security ---

def protect(work: Path, inputs: list[Path], p: dict) -> Result:
    pw = str(p.get("password_new") or "")
    if len(pw) < 4:
        raise ToolError("password must be at least 4 characters")
    src = open_pdf(inputs[0], p.get("password", ""))
    perm = (fitz.PDF_PERM_ACCESSIBILITY | fitz.PDF_PERM_PRINT)
    if p.get("allow_copy"):
        perm |= fitz.PDF_PERM_COPY
    if p.get("allow_modify"):
        perm |= fitz.PDF_PERM_MODIFY | fitz.PDF_PERM_ANNOTATE
    base = stem(inputs[0])
    dest = work / f"{base}_protected.pdf"
    src.save(dest, encryption=fitz.PDF_ENCRYPT_AES_256,
             owner_pw=str(p.get("owner_password") or pw),
             user_pw=pw, permissions=perm, garbage=4, deflate=True)
    src.close()
    return Result(dest, PDF, f"{base}_protected.pdf")


def unlock(work: Path, inputs: list[Path], p: dict) -> Result:
    """Removes encryption from a PDF you can already open.

    This is not a cracker: without the correct password the file stays shut.
    """
    src = open_pdf(inputs[0], p.get("password", ""))
    base = stem(inputs[0])
    dest = work / f"{base}_unlocked.pdf"
    src.save(dest, encryption=fitz.PDF_ENCRYPT_NONE, garbage=4, deflate=True)
    src.close()
    return Result(dest, PDF, f"{base}_unlocked.pdf")


def redact(work: Path, inputs: list[Path], p: dict) -> Result:
    """Permanently destroys matched content -- pixels and underlying text.

    apply_redactions rewrites the content stream, so the text cannot be
    recovered by selecting under the box (the classic fake-redaction failure).
    """
    terms = [t for t in (p.get("terms") or "").splitlines() if t.strip()]
    if not terms:
        raise ToolError("supply at least one term to redact, one per line")
    src = open_pdf(inputs[0], p.get("password", ""))
    fill = _hex_rgb(p.get("color") or "#000000")
    hits = 0
    for pno in parse_pages(p.get("pages", ""), src.page_count):
        page = src[pno]
        for term in terms:
            for rect in page.search_for(term.strip(), quads=False):
                page.add_redact_annot(rect, fill=fill)
                hits += 1
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS)
    if not hits:
        src.close()
        raise ToolError("none of those terms were found in the text layer")
    base = stem(inputs[0])
    dest = save(src, work / f"{base}_redacted.pdf")
    src.close()
    return Result(dest, PDF, f"{base}_redacted.pdf")


def compare(work: Path, inputs: list[Path], p: dict) -> Result:
    """Word-level diff of the two documents' text layers, as Markdown."""
    import difflib
    if len(inputs) != 2:
        raise ToolError("compare needs exactly 2 files")
    texts = []
    for f in inputs:
        d = open_pdf(f, p.get("password", ""))
        texts.append("\n".join(pg.get_text("text") for pg in d).splitlines())
        d.close()
    diff = difflib.unified_diff(texts[0], texts[1],
                                fromfile=inputs[0].name, tofile=inputs[1].name,
                                lineterm="", n=2)
    body = "\n".join(diff) or "(the two text layers are identical)"
    dest = work / "comparison.md"
    dest.write_text(f"# Comparison\n\n```diff\n{body}\n```\n", encoding="utf-8")
    return Result(dest, "text/markdown", "comparison.md")


# ------------------------------------------------------------- extract ---

def extract_images(work: Path, inputs: list[Path], p: dict) -> Result:
    """Pull out embedded images at their ORIGINAL resolution.

    Deliberately different from pdf-to-jpg, which rasterises whole pages at a
    chosen DPI. Here we recover the image objects the PDF actually carries, so
    a 4000px photo comes back as 4000px rather than downsampled to the page.
    """
    src = open_pdf(inputs[0], p.get("password", ""))
    base = stem(inputs[0])
    min_px = max(0, int(p.get("min_size", 64)))
    seen: set[int] = set()
    files: list[Path] = []
    for pno in parse_pages(p.get("pages", ""), src.page_count):
        for info in src[pno].get_images(full=True):
            xref = info[0]
            if xref in seen:      # the same logo on 200 pages is one file
                continue
            seen.add(xref)
            try:
                img = src.extract_image(xref)
            except Exception as exc:
                log.warning("xref %s not extractable: %s", xref, exc)
                continue
            if img["width"] < min_px or img["height"] < min_px:
                continue          # skip rules, bullets and spacer pixels
            f = work / f"{base}_p{pno + 1}_{xref}.{img['ext']}"
            f.write_bytes(img["image"])
            files.append(f)
    src.close()
    if not files:
        raise ToolError("no embedded images found above the minimum size")
    if len(files) == 1:
        return Result(files[0], f"image/{files[0].suffix.lstrip('.')}", files[0].name)
    dest = zip_dir(files, work / f"{base}_images.zip")
    return Result(dest, ZIP, f"{base}_images.zip")


def extract_attachments(work: Path, inputs: list[Path], p: dict) -> Result:
    src = open_pdf(inputs[0], p.get("password", ""))
    files: list[Path] = []
    for i in range(src.embfile_count()):
        info = src.embfile_info(i)
        name = safe_component(info.get("filename") or f"attachment_{i}")
        f = work / name
        f.write_bytes(src.embfile_get(i))
        files.append(f)
    src.close()
    if not files:
        raise ToolError("this PDF has no file attachments")
    if len(files) == 1:
        return Result(files[0], "application/octet-stream", files[0].name)
    dest = zip_dir(files, work / "attachments.zip")
    return Result(dest, ZIP, "attachments.zip")


def safe_component(name: str) -> str:
    # os.path.basename does NOT split on backslashes on Linux, so a Windows
    # path (`..\..\system32`) would arrive whole and its `..` survive. Normalise
    # both separators first, then drop leading dots so no `..` can remain.
    name = str(name).replace("\\", "/")
    name = os.path.basename(name)
    name = re.sub(r"[^\w.\- ]+", "_", name).lstrip(".")
    return name[:100] or "file"


# ------------------------------------------------------------ metadata ---

META_KEYS = ("title", "author", "subject", "keywords", "creator", "producer")


def metadata(work: Path, inputs: list[Path], p: dict) -> Result:
    """Edit or strip document metadata.

    Stripping also clears the XML metadata stream, which is the part people
    forget: clearing the info dictionary alone leaves author and software
    fingerprints sitting in the XMP packet.
    """
    src = open_pdf(inputs[0], p.get("password", ""))
    base = stem(inputs[0])
    if p.get("strip"):
        src.set_metadata({})
        src.del_xml_metadata()
    else:
        meta = dict(src.metadata or {})
        for k in META_KEYS:
            if p.get(k) is not None:
                meta[k] = str(p.get(k))
        src.set_metadata(meta)
    dest = save(src, work / f"{base}_metadata.pdf")
    src.close()
    return Result(dest, PDF, f"{base}_metadata.pdf")


def flatten(work: Path, inputs: list[Path], p: dict) -> Result:
    """Bake annotations and form fields into the page content.

    After this, a filled form cannot be un-filled and a highlight cannot be
    dragged off. Note this is about *editability*, not confidentiality -- to
    actually destroy content, use Redact.
    """
    src = open_pdf(inputs[0], p.get("password", ""))
    try:
        src.bake(annots=True, widgets=True)
    except AttributeError:
        raise ToolError("flatten needs PyMuPDF 1.24.6 or newer")
    base = stem(inputs[0])
    dest = save(src, work / f"{base}_flat.pdf")
    src.close()
    return Result(dest, PDF, f"{base}_flat.pdf")


# ------------------------------------------------------------- imposition ---

def n_up(work: Path, inputs: list[Path], p: dict) -> Result:
    """Place 2 or 4 source pages on each output sheet."""
    src = open_pdf(inputs[0], p.get("password", ""))
    per = int(p.get("per_sheet", 2))
    if per not in (2, 4):
        raise ToolError("pages per sheet must be 2 or 4")
    gap = max(0, min(72, float(p.get("gap", 8))))
    first = src[0].rect
    if per == 2:
        # Two portrait pages side by side make a landscape sheet.
        sheet_w, sheet_h, cols, rows = first.height, first.width, 2, 1
    else:
        sheet_w, sheet_h, cols, rows = first.width, first.height, 2, 2

    out = fitz.open()
    pages = parse_pages(p.get("pages", ""), src.page_count)
    for i in range(0, len(pages), per):
        sheet = out.new_page(width=sheet_w, height=sheet_h)
        cell_w = (sheet_w - gap * (cols + 1)) / cols
        cell_h = (sheet_h - gap * (rows + 1)) / rows
        for slot, pno in enumerate(pages[i:i + per]):
            cx, cy = slot % cols, slot // cols
            box = fitz.Rect(
                gap + cx * (cell_w + gap), gap + cy * (cell_h + gap),
                gap + cx * (cell_w + gap) + cell_w,
                gap + cy * (cell_h + gap) + cell_h,
            )
            # keep_proportion stops a portrait page being stretched to fill a
            # landscape cell.
            sheet.show_pdf_page(box, src, pno, keep_proportion=True)
    src.close()
    base = stem(inputs[0])
    dest = save(out, work / f"{base}_{per}up.pdf")
    out.close()
    return Result(dest, PDF, f"{base}_{per}up.pdf")


def split_bookmarks(work: Path, inputs: list[Path], p: dict) -> Result:
    """Split at outline entries -- no page numbers to look up."""
    src = open_pdf(inputs[0], p.get("password", ""))
    level = max(1, int(p.get("level", 1)))
    toc = [t for t in src.get_toc() if t[0] <= level]
    if not toc:
        src.close()
        raise ToolError(f"no bookmarks at level {level} or above")

    starts = sorted({max(0, t[2] - 1) for t in toc})
    titles = {max(0, t[2] - 1): t[1] for t in toc}
    base = stem(inputs[0])
    parts: list[Path] = []
    for idx, start in enumerate(starts):
        end = (starts[idx + 1] - 1) if idx + 1 < len(starts) else src.page_count - 1
        if end < start:
            continue
        out = fitz.open()
        out.insert_pdf(src, from_page=start, to_page=end)
        label = safe_component(titles.get(start, f"section{idx + 1}"))[:60]
        part = save(out, work / f"{idx + 1:02d}_{label}.pdf")
        out.close()
        parts.append(part)
    src.close()
    if len(parts) == 1:
        return Result(parts[0], PDF, parts[0].name)
    dest = zip_dir(parts, work / f"{base}_sections.zip")
    return Result(dest, ZIP, f"{base}_sections.zip")


def header_footer(work: Path, inputs: list[Path], p: dict) -> Result:
    """Arbitrary running text. Generalises the page-numbers tool."""
    import datetime
    src = open_pdf(inputs[0], p.get("password", ""))
    template = str(p.get("text") or "").strip()
    if not template:
        raise ToolError("text is required")
    size = max(6, min(72, int(p.get("size", 10))))
    color = _hex_rgb(p.get("color") or "#555555")
    fx, fy = POSITIONS.get(p.get("position", "top"), (0.5, 0.08))
    pages = parse_pages(p.get("pages", ""), src.page_count)
    today = datetime.date.today().isoformat()
    for seq, pno in enumerate(pages):
        page = src[pno]
        label = (template
                 .replace("{n}", str(pno + 1))
                 .replace("{total}", str(src.page_count))
                 .replace("{date}", today)
                 .replace("{filename}", stem(inputs[0])))
        w = fitz.get_text_length(label, fontname="helv", fontsize=size)
        page.insert_text(fitz.Point(page.rect.width * fx - w / 2,
                                    page.rect.height * fy),
                         label, fontsize=size, fontname="helv",
                         color=color, overlay=True)
    base = stem(inputs[0])
    dest = save(src, work / f"{base}_headed.pdf")
    src.close()
    return Result(dest, PDF, f"{base}_headed.pdf")


def grayscale(work: Path, inputs: list[Path], p: dict) -> Result:
    """Convert to grayscale via Ghostscript. Often a large size win too."""
    if not inputs[0].stat().st_size:
        raise ToolError(f"cannot read {inputs[0].name}: file is empty")
    base = stem(inputs[0])
    dest = work / f"{base}_gray.pdf"
    run([
        "gs", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.7",
        "-sColorConversionStrategy=Gray",
        "-dProcessColorModel=/DeviceGray",
        "-dOverrideICC=true", "-dNOPAUSE", "-dQUIET", "-dBATCH",
        f"-sOutputFile={dest}", str(inputs[0]),
    ], cwd=work)
    return Result(dest, PDF, f"{base}_gray.pdf")


def rasterise(work: Path, inputs: list[Path], p: dict) -> Result:
    """Rebuild every page as a flat image.

    Blunt but effective: it destroys embedded JavaScript, form fields,
    hyperlinks and any selectable text, which makes it a decent sanitiser for
    a PDF from an untrusted source. It also destroys accessibility and search,
    so it is a deliberate trade rather than a default.
    """
    src = open_pdf(inputs[0], p.get("password", ""))
    dpi = max(72, min(400, int(p.get("dpi", 150))))
    pages = parse_pages(p.get("pages", ""), src.page_count)
    dpi = clamp_dpi(pages, src, dpi)
    out = fitz.open()
    for pno in pages:
        page = src[pno]
        pix = page.get_pixmap(dpi=dpi)
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, pixmap=pix)
    src.close()
    base = stem(inputs[0])
    dest = save(out, work / f"{base}_flattened.pdf")
    out.close()
    return Result(dest, PDF, f"{base}_flattened.pdf")


def _hex_rgb(value: str) -> tuple[float, float, float]:
    # Strict: an empty or None colour is a caller bug, not black. Callers that
    # want a default pass it explicitly (`p.get("color") or "#000000"`), so a
    # blank field falls back there while a truly missing value is caught here.
    if value is None or str(value).strip() == "":
        raise ToolError("colour is required")
    v = str(value).strip().lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    if len(v) != 6 or not re.fullmatch(r"[0-9a-fA-F]{6}", v):
        raise ToolError(f"bad colour: {value!r}")
    return tuple(int(v[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore


# -------------------------------------------------------------- registry ---

@dataclass
class Tool:
    key: str
    name: str
    group: str
    blurb: str
    fn: Callable[[Path, list[Path], dict], Result]
    accept: str = ".pdf"
    multi: bool = False       # accepts more than one file
    min_files: int = 1
    fields: list[dict[str, Any]] | None = None


def F(name: str, kind: str, label: str, **kw) -> dict:
    return {"name": name, "kind": kind, "label": label, **kw}


PAGES = F("pages", "text", "Pages", placeholder="all, or 1-3,7,10-",
          help="Blank means every page.")

TOOLS: list[Tool] = [
    # -- organize
    Tool("merge", "Merge PDF", "Organize",
         "Combine several PDFs into one, in the order you arrange them.",
         merge, multi=True, min_files=2),
    Tool("split", "Split PDF", "Organize",
         "Pull out a range, or burst the file into separate documents.",
         split, fields=[
             F("mode", "select", "Mode", options=[
                 ["ranges", "Extract selected pages into one PDF"],
                 ["every", "One PDF per page"],
                 ["chunks", "Fixed-size groups"]], default="ranges"),
             PAGES,
             F("size", "number", "Pages per group", default=2, min=1, max=500),
         ]),
    Tool("remove-pages", "Remove pages", "Organize",
         "Delete the pages you name and keep everything else.",
         remove_pages, fields=[PAGES]),
    Tool("organize", "Reorder pages", "Organize",
         "Rebuild the document in an explicit page order.",
         organize, fields=[
             F("pages", "text", "New order", placeholder="3,1,2,4-",
               help="Pages appear in exactly this order."),
         ]),
    Tool("rotate", "Rotate PDF", "Organize",
         "Turn pages a quarter, half or three-quarter turn.",
         rotate, fields=[
             F("angle", "select", "Rotation", options=[
                 ["90", "90 clockwise"], ["180", "180"],
                 ["270", "90 counter-clockwise"]], default="90"),
             PAGES,
         ]),
    Tool("split-bookmarks", "Split at bookmarks", "Organize",
         "Break a long document into sections using its own outline.",
         split_bookmarks, fields=[
             F("level", "select", "Split at", options=[
                 ["1", "Top-level bookmarks only"],
                 ["2", "Level 2 and above"],
                 ["3", "Level 3 and above"]], default="1"),
         ]),
    Tool("n-up", "N-up / booklet", "Organize",
         "Print two or four pages per sheet to save paper.",
         n_up, fields=[
             F("per_sheet", "select", "Pages per sheet",
               options=[["2", "2 up"], ["4", "4 up"]], default="2"),
             F("gap", "number", "Gap (pt)", default=8, min=0, max=72),
             PAGES,
         ]),
    # -- optimize
    Tool("compress", "Compress PDF", "Optimize",
         "Shrink the file by downsampling images and deduplicating resources.",
         compress, fields=[
             F("level", "select", "Compression", options=[
                 ["low", "Less compression, best quality"],
                 ["recommended", "Recommended"],
                 ["extreme", "Extreme, smallest file"]], default="recommended"),
         ]),
    Tool("repair", "Repair PDF", "Optimize",
         "Rebuild a damaged cross-reference table and recover what is readable.",
         repair),
    Tool("grayscale", "Grayscale PDF", "Optimize",
         "Strip colour for cheaper printing, usually a big size win too.",
         grayscale),
    Tool("ocr", "OCR PDF", "Optimize",
         "Add a searchable, selectable text layer to a scan.",
         ocr, fields=[
             F("lang", "select", "Language", options=[
                 ["eng", "English"], ["fra", "French"], ["deu", "German"],
                 ["spa", "Spanish"], ["por", "Portuguese"], ["ita", "Italian"]],
               default="eng"),
             F("deskew", "checkbox", "Straighten crooked scans"),
             F("force", "checkbox", "Re-OCR pages that already have text"),
         ]),
    # -- convert
    Tool("jpg-to-pdf", "Image to PDF", "Convert to PDF",
         "Turn JPG, PNG or WEBP images into a PDF, one image per page.",
         jpg_to_pdf, accept=".jpg,.jpeg,.png,.webp,.bmp,.tif,.tiff",
         multi=True, fields=[
             F("size", "select", "Page size", options=[
                 ["fit", "Fit to each image"], ["a4", "A4"],
                 ["letter", "US Letter"]], default="fit"),
             F("margin", "number", "Margin (pt)", default=0, min=0, max=200),
         ]),
    Tool("office-to-pdf", "Office to PDF", "Convert to PDF",
         "Word, Excel and PowerPoint files rendered to PDF by LibreOffice.",
         office_to_pdf, accept=",".join(sorted(OFFICE_EXT)), multi=True),
    Tool("pdf-to-jpg", "PDF to image", "Convert from PDF",
         "Render each page as a JPG or PNG at the resolution you choose.",
         pdf_to_jpg, fields=[
             F("format", "select", "Format",
               options=[["jpg", "JPG"], ["png", "PNG"]], default="jpg"),
             F("dpi", "number", "Resolution (DPI)", default=150, min=36, max=600),
             PAGES,
         ]),
    Tool("pdf-to-word", "PDF to Word", "Convert from PDF",
         "Rebuild paragraphs, tables and images as an editable .docx.",
         pdf_to_word),
    Tool("pdf-to-excel", "PDF to Excel", "Convert from PDF",
         "Extract detected tables into a worksheet each.",
         pdf_to_excel, fields=[PAGES]),
    Tool("pdf-to-powerpoint", "PDF to PowerPoint", "Convert from PDF",
         "One slide per page, rendered full bleed.",
         pdf_to_powerpoint, fields=[
             F("dpi", "number", "Resolution (DPI)", default=150, min=72, max=300),
             PAGES,
         ]),
    Tool("pdf-to-pdfa", "PDF to PDF/A", "Convert from PDF",
         "Convert to the PDF/A-2b archival standard.", pdf_to_pdfa),
    Tool("pdf-to-markdown", "PDF to Markdown", "Convert from PDF",
         "Extract the text layer as Markdown, page by page.",
         pdf_to_markdown, fields=[PAGES]),
    Tool("extract-images", "Extract images", "Convert from PDF",
         "Recover embedded images at their original resolution.",
         extract_images, fields=[
             F("min_size", "number", "Ignore images smaller than (px)",
               default=64, min=0, max=4000,
               help="Filters out rules, bullets and spacer pixels."),
             PAGES,
         ]),
    Tool("extract-attachments", "Extract attachments", "Convert from PDF",
         "Pull out files embedded inside the PDF.",
         extract_attachments),
    # -- edit
    Tool("watermark", "Add watermark", "Edit",
         "Stamp text across the page, once or tiled.",
         watermark, fields=[
             F("text", "text", "Watermark text", placeholder="CONFIDENTIAL",
               required=True),
             F("mode", "select", "Layout", options=[
                 ["single", "Once per page"], ["tile", "Tiled"]],
               default="single"),
             F("position", "select", "Position",
               options=[[k, k.replace("-", " ")] for k in POSITIONS],
               default="center"),
             F("size", "number", "Font size", default=42, min=6, max=200),
             F("opacity", "number", "Opacity", default=0.25, min=0.05, max=1,
               step=0.05),
             F("angle", "number", "Angle", default=45, min=-180, max=180),
             F("color", "color", "Colour", default="#7c5cff"),
             PAGES,
         ]),
    Tool("page-numbers", "Add page numbers", "Edit",
         "Number the pages, with your own format and placement.",
         page_numbers, fields=[
             F("format", "text", "Format", default="{n}",
               help="{n} is the number, {total} the count. e.g. Page {n} of {total}"),
             F("position", "select", "Position",
               options=[[k, k.replace("-", " ")] for k in POSITIONS],
               default="bottom"),
             F("start", "number", "Start at", default=1, min=0, max=99999),
             F("size", "number", "Font size", default=11, min=6, max=72),
             F("color", "color", "Colour", default="#000000"),
             PAGES,
         ]),
    Tool("crop", "Crop PDF", "Edit",
         "Trim margins off every page, measured in points.",
         crop, fields=[
             F("top", "number", "Top (pt)", default=0, min=0, max=1000),
             F("bottom", "number", "Bottom (pt)", default=0, min=0, max=1000),
             F("left", "number", "Left (pt)", default=0, min=0, max=1000),
             F("right", "number", "Right (pt)", default=0, min=0, max=1000),
             PAGES,
         ]),
    Tool("header-footer", "Header / footer", "Edit",
         "Running text at the top or bottom, with date and filename tokens.",
         header_footer, fields=[
             F("text", "text", "Text", required=True,
               placeholder="Draft — {date} — page {n} of {total}",
               help="Tokens: {n} {total} {date} {filename}"),
             F("position", "select", "Position",
               options=[[k, k.replace("-", " ")] for k in POSITIONS],
               default="top"),
             F("size", "number", "Font size", default=10, min=6, max=72),
             F("color", "color", "Colour", default="#555555"),
             PAGES,
         ]),
    Tool("flatten", "Flatten PDF", "Edit",
         "Bake annotations and form fields in so they can no longer be edited.",
         flatten),
    Tool("metadata", "Edit metadata", "Edit",
         "Change the title and author, or strip identifying metadata entirely.",
         metadata, fields=[
             F("strip", "checkbox", "Strip everything (ignores the fields below)"),
             F("title", "text", "Title"),
             F("author", "text", "Author"),
             F("subject", "text", "Subject"),
             F("keywords", "text", "Keywords"),
         ]),
    # -- security
    Tool("rasterise", "Rasterise PDF", "Security",
         "Rebuild every page as a flat image, destroying scripts and form fields.",
         rasterise, fields=[
             F("dpi", "number", "Resolution (DPI)", default=150, min=72, max=400),
             PAGES,
         ]),
    Tool("protect", "Protect PDF", "Security",
         "Encrypt with AES-256 and set what readers are allowed to do.",
         protect, fields=[
             F("password_new", "password", "New password", required=True),
             F("allow_copy", "checkbox", "Allow copying text"),
             F("allow_modify", "checkbox", "Allow editing and annotation"),
         ]),
    Tool("unlock", "Unlock PDF", "Security",
         "Strip encryption from a PDF whose password you know.",
         unlock),
    Tool("redact", "Redact PDF", "Security",
         "Permanently erase matching text -- not a black box drawn on top.",
         redact, fields=[
             F("terms", "textarea", "Terms to redact", required=True,
               placeholder="one term per line"),
             F("color", "color", "Box colour", default="#000000"),
             PAGES,
         ]),
    Tool("compare", "Compare PDFs", "Security",
         "Line-by-line diff of two documents' text.",
         compare, multi=True, min_files=2),
]

REGISTRY = {t.key: t for t in TOOLS}
