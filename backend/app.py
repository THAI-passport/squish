"""Squish -- every PDF tool, on your own machine.

Stateless FastAPI service. One request does the whole job: upload, process,
stream the result back, delete the scratch directory. Nothing touches disk
outside a per-request temp dir and nothing survives the response.

  GET  /api/health           liveness/readiness + version stamp
  GET  /api/tools            tool registry, drives the UI
  POST /api/t/{tool}         multipart: files[] + option fields -> file download

Run:
  ./run-local.sh             venv or docker, with a version check
  docker compose up --build  http://localhost:8000
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
import time
import unicodedata
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
# Import UploadFile from Starlette, NOT FastAPI. request.form() yields
# starlette.datastructures.UploadFile; fastapi.UploadFile is a *subclass*, so
# `isinstance(form_file, fastapi.UploadFile)` is False and every upload gets
# filtered out as "no files uploaded". The base class matches both.
from starlette.datastructures import UploadFile
from starlette.background import BackgroundTask

try:
    from starlette.formparsers import MultiPartException
except ImportError:                       # older Starlette
    class MultiPartException(Exception):  # type: ignore[no-redef]
        pass

import tools as T

log = logging.getLogger("uvicorn.error")

APP_TITLE = "Squish"
# run-local.sh greps for the "-squish" marker to prove new code is running.
APP_VERSION = "1.2.1-squish"

# ---------------------------------------------------------------- config ---
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "200"))
# Cap for the whole multipart body, not just one part. This used to be an
# implicit `MAX_UPLOAD_MB * 2` buried in store(), so a 40-file merge failed for
# a reason no limit in /api/health could explain. Same default, now named,
# reported, and enforced before the body is read.
#
# It cannot be raised alone: the body is spooled and then copied into the
# scratch dir, so peak scratch use is roughly 2x this. Move it together with
# the compose tmpfs size, the k8s emptyDir sizeLimit, and the ingress
# proxy-body-size annotation.
MAX_TOTAL_UPLOAD_MB = int(os.environ.get("MAX_TOTAL_UPLOAD_MB",
                                         str(MAX_UPLOAD_MB * 2)))
MAX_FILES = int(os.environ.get("MAX_FILES", "40"))
MAX_CONCURRENCY = int(os.environ.get("MAX_CONCURRENCY", "4"))
JOB_TIMEOUT = int(os.environ.get("JOB_TIMEOUT", "900"))
# Backstop on the way out. tools.py prevents most runaway output by clamping
# render resolution, but a tool could still produce something enormous, and
# /tmp is a tmpfs -- writing 8 GB there is writing 8 GB of RAM.
MAX_OUTPUT_MB = int(os.environ.get("MAX_OUTPUT_MB", "1024"))
API_KEY = os.environ.get("API_KEY") or None
# Under a read-only root filesystem this must point at a writable emptyDir.
SCRATCH = os.environ.get("SCRATCH_DIR", tempfile.gettempdir())

STATIC_DIR = Path(__file__).parent / "static"
app = FastAPI(title=APP_TITLE, docs_url=None, redoc_url=None)

# Heavy tools (LibreOffice, Ghostscript, tesseract) are CPU-bound and memory
# hungry. Without a ceiling, a handful of concurrent OCR jobs will OOM the pod.
sem = asyncio.Semaphore(MAX_CONCURRENCY)


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    if API_KEY and path.startswith("/api") and path != "/api/health":
        given = request.headers.get("x-api-key") or request.query_params.get("key")
        if given != API_KEY:
            return JSONResponse({"detail": "invalid or missing API key"}, 401)
    # Reject an oversized body BEFORE Starlette parses the multipart. Once
    # request.form() runs, the whole body has already been received and spooled
    # -- and the spool lands in /tmp, which is a tmpfs in both deployments, so
    # a rejected 8 GB upload would still have cost 8 GB of RAM first.
    if path.startswith("/api/t/"):
        declared = request.headers.get("content-length")
        if declared and declared.isdigit():
            if int(declared) > MAX_TOTAL_UPLOAD_MB * 1024 * 1024:
                return JSONResponse(
                    {"detail": f"request body exceeds {MAX_TOTAL_UPLOAD_MB} MB"}, 413)
    started = time.monotonic()
    response = await call_next(request)
    if path.startswith("/api/t/"):
        log.info("%s -> %s in %.1fs", path, response.status_code,
                 time.monotonic() - started)
    return response


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "version": APP_VERSION,
        "tools": len(T.TOOLS),
        "max_upload_mb": MAX_UPLOAD_MB,
        "max_total_upload_mb": MAX_TOTAL_UPLOAD_MB,
        "max_output_mb": MAX_OUTPUT_MB,
        "max_files": MAX_FILES,
        "max_pages": T.MAX_PAGES,
        "max_render_mp": T.MAX_RENDER_MP,
        "max_concurrency": MAX_CONCURRENCY,
        "api_key": bool(API_KEY),
        "engines": engines(),
    }


def engines() -> dict[str, bool]:
    """Which optional binaries this image actually has.

    The UI greys out tools whose engine is missing instead of letting the user
    submit a job that is guaranteed to fail.
    """
    return {name: shutil.which(name) is not None
            for name in ("gs", "soffice", "tesseract", "ocrmypdf", "qpdf")}


# ------------------------------------------------------------- metrics ---
# Deliberately not prometheus_client: this is a handful of counters and adding
# a dependency (plus its multiprocess-mode footguns) to emit them is not worth
# it. Plain text in the Prometheus exposition format scrapes identically.
STATS: dict[tuple[str, str], int] = {}
DURATION: dict[str, list[float]] = {}


def note(tool: str, outcome: str, seconds: float) -> None:
    STATS[(tool, outcome)] = STATS.get((tool, outcome), 0) + 1
    # Keep a bounded window; an unbounded list is a slow memory leak in a
    # process that is meant to run for weeks.
    d = DURATION.setdefault(tool, [])
    d.append(seconds)
    if len(d) > 200:
        del d[:-200]


@app.get("/metrics")
async def metrics():
    from fastapi.responses import PlainTextResponse
    lines = [
        "# HELP squish_jobs_total Jobs by tool and outcome.",
        "# TYPE squish_jobs_total counter",
    ]
    for (tool, outcome), n in sorted(STATS.items()):
        lines.append(f'squish_jobs_total{{tool="{tool}",outcome="{outcome}"}} {n}')
    lines += [
        "# HELP squish_job_seconds Recent job duration by tool.",
        "# TYPE squish_job_seconds summary",
    ]
    for tool, ds in sorted(DURATION.items()):
        if not ds:
            continue
        ordered = sorted(ds)
        p50 = ordered[len(ordered) // 2]
        p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
        lines.append(f'squish_job_seconds{{tool="{tool}",quantile="0.5"}} {p50:.3f}')
        lines.append(f'squish_job_seconds{{tool="{tool}",quantile="0.95"}} {p95:.3f}')
    # Saturation: if this sits at MAX_CONCURRENCY, requests are queueing and
    # the CPU-based HPA target may be lying to you.
    inflight = MAX_CONCURRENCY - sem._value       # noqa: SLF001
    lines += [
        "# HELP squish_inflight Jobs currently holding the semaphore.",
        "# TYPE squish_inflight gauge",
        f"squish_inflight {max(0, inflight)}",
        "# HELP squish_concurrency_limit Configured ceiling.",
        "# TYPE squish_concurrency_limit gauge",
        f"squish_concurrency_limit {MAX_CONCURRENCY}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")


@app.get("/api/tools")
async def tool_list():
    have = engines()
    needs = {
        "compress": "gs", "grayscale": "gs", "repair": "qpdf",
        "ocr": "ocrmypdf", "pdf-to-pdfa": "ocrmypdf",
        "office-to-pdf": "soffice",
    }
    return {
        "version": APP_VERSION,
        "limits": {"max_upload_mb": MAX_UPLOAD_MB,
                   "max_total_upload_mb": MAX_TOTAL_UPLOAD_MB,
                   "max_files": MAX_FILES},
        "tools": [
            {
                "key": t.key, "name": t.name, "group": t.group, "blurb": t.blurb,
                "accept": t.accept, "multi": t.multi, "min_files": t.min_files,
                "fields": t.fields or [],
                "available": have.get(needs.get(t.key, ""), True),
                "needs": needs.get(t.key),
            }
            for t in T.TOOLS
        ],
    }


def discard(work: Path) -> None:
    """Delete a scratch dir now.

    NOT BackgroundTask: Starlette's BackgroundTask.__call__ is a coroutine, so
    calling it synchronously builds a coroutine object that nobody awaits and
    the directory survives. Every error path used to leak its temp dir into a
    tmpfs -- i.e. into RAM -- for the lifetime of the pod.
    """
    shutil.rmtree(work, ignore_errors=True)


# Which keyword arguments this Starlette's request.form() actually accepts.
# Decided ONCE at import from the signature, never by calling and catching:
# request.form() consumes the body stream, so a call that raised TypeError on
# an unsupported kwarg could leave the stream half-read, and the retry would
# then parse an empty body and see zero files -- turning every upload into a
# spurious "no files uploaded" 400.
def _supported_form_kwargs() -> dict:
    import inspect
    try:
        params = inspect.signature(Request.form).parameters
    except (TypeError, ValueError):
        return {}
    kw = {}
    if "max_files" in params:
        kw["max_files"] = MAX_FILES
    if "max_part_size" in params:
        kw["max_part_size"] = MAX_UPLOAD_MB * 1024 * 1024
    return kw


_FORM_KWARGS = _supported_form_kwargs()


async def parse_form(request: Request):
    """Parse multipart, applying per-part and part-count ceilings if supported.

    The middleware Content-Length check is the version-independent backstop; the
    kwargs here are the precise per-part limit where Starlette offers it.
    """
    try:
        return await request.form(**_FORM_KWARGS)
    except MultiPartException:
        # Starlette signals "a part blew the ceiling" this way; unhandled it
        # surfaces as a vague 400 about parsing the body.
        raise HTTPException(413, f"a file exceeds the {MAX_UPLOAD_MB} MB limit")


@app.post("/api/t/{key}")
async def run_tool(key: str, request: Request):
    tool = T.REGISTRY.get(key)
    if tool is None:
        raise HTTPException(404, f"unknown tool: {key}")

    form = await parse_form(request)
    uploads = [v for v in form.getlist("files") if isinstance(v, UploadFile)]
    if not uploads:
        raise HTTPException(400, "no files uploaded")
    if len(uploads) < tool.min_files:
        raise HTTPException(400, f"{tool.name} needs at least {tool.min_files} files")
    if len(uploads) > MAX_FILES:
        raise HTTPException(400, f"too many files (limit {MAX_FILES})")
    if not tool.multi and len(uploads) > 1:
        uploads = uploads[:1]

    params = {k: v for k, v in form.multi_items()
              if k != "files" and isinstance(v, str)}

    work = Path(tempfile.mkdtemp(prefix=f"squish-{key}-", dir=SCRATCH))
    t0 = time.monotonic()
    job = None
    try:
        inputs = await store(uploads, work)
        # A timeout cannot cancel a running thread: asyncio.to_thread has no
        # way to interrupt the worker. The old code released the semaphore on
        # timeout while the thread kept burning CPU, so enough timeouts made
        # MAX_CONCURRENCY a fiction. Hold the slot until the thread is really
        # finished, and shield the task so wait_for gives up on waiting rather
        # than pretending to cancel.
        await sem.acquire()
        job = asyncio.ensure_future(
            asyncio.to_thread(tool.fn, work, inputs, params))
        job.add_done_callback(lambda _f: sem.release())
        result = await asyncio.wait_for(asyncio.shield(job), timeout=JOB_TIMEOUT)
    except T.ToolError as exc:
        discard(work)
        note(key, "rejected", time.monotonic() - t0)
        raise HTTPException(400, str(exc))
    except asyncio.TimeoutError:
        # Do NOT delete work yet: the thread is still writing into it. Defer
        # the removal until the orphan actually finishes.
        job.add_done_callback(lambda _f, w=work: discard(w))
        note(key, "timeout", time.monotonic() - t0)
        log.warning("tool %s timed out; worker thread is still running", key)
        raise HTTPException(504, f"{tool.name} exceeded {JOB_TIMEOUT}s")
    except HTTPException:
        discard(work)
        note(key, "rejected", time.monotonic() - t0)
        raise
    except Exception:
        log.exception("tool %s crashed", key)
        discard(work)
        note(key, "error", time.monotonic() - t0)
        raise HTTPException(500, "processing failed -- see server logs")

    cleanup = BackgroundTask(shutil.rmtree, work, ignore_errors=True)
    out_mb = result.path.stat().st_size / 1048576
    if out_mb > MAX_OUTPUT_MB:
        discard(work)
        note(key, "too_large", time.monotonic() - t0)
        raise HTTPException(
            507, f"result is {out_mb:.0f} MB, over the {MAX_OUTPUT_MB} MB limit "
                 f"-- select fewer pages or a lower resolution")
    note(key, "ok", time.monotonic() - t0)

    return FileResponse(
        result.path,
        media_type=result.media_type,
        filename=result.filename,
        headers={
            "Content-Disposition": f'attachment; filename="{ascii_name(result.filename)}"',
            "X-Squish-Tool": key,
            "Cache-Control": "no-store",
        },
        background=cleanup,   # scratch dir dies once the bytes are on the wire
    )


async def store(uploads: list[UploadFile], work: Path) -> list[Path]:
    """Copy the parsed uploads to the scratch dir in chunks, enforcing caps.

    By the time this runs Starlette has already received the body, so this is
    the second line of defence, not the first -- the middleware Content-Length
    check and parse_form()'s max_part_size are what stop an oversized body from
    being spooled at all. Chunked copying still matters: reading a whole part
    into memory here would double peak usage for no reason.
    """
    limit = MAX_UPLOAD_MB * 1024 * 1024
    total_limit = MAX_TOTAL_UPLOAD_MB * 1024 * 1024
    total = 0
    paths: list[Path] = []
    for idx, up in enumerate(uploads):
        name = safe_filename(up.filename or f"file{idx}")
        dest = work / f"{idx:03d}_{name}"
        size = 0
        with dest.open("wb") as fh:
            while chunk := await up.read(1 << 20):
                size += len(chunk)
                total += len(chunk)
                if size > limit:
                    raise HTTPException(
                        413, f"{name} exceeds the {MAX_UPLOAD_MB} MB per-file limit")
                if total > total_limit:
                    raise HTTPException(
                        413, f"the batch exceeds the {MAX_TOTAL_UPLOAD_MB} MB total limit")
                fh.write(chunk)
        if size == 0:
            raise HTTPException(400, f"{name} is empty")
        paths.append(dest)
    return paths


def safe_filename(name: str) -> str:
    """Strip directory components and anything that could escape the temp dir."""
    name = os.path.basename(name).replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^\w.\- ]+", "_", name).strip(". ")
    return name[:120] or "upload"


def ascii_name(name: str) -> str:
    """Content-Disposition is latin-1 only; a stray emoji here is a 500."""
    ascii_only = unicodedata.normalize("NFKD", name).encode("ascii", "ignore")
    return ascii_only.decode() or "download"


# The UI is one static file. Mount it AFTER the API routes so /api always wins,
# and no-cache the entry point so a redeploy is never masked by a stale page.
if STATIC_DIR.is_dir():

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC_DIR / "index.html",
                            headers={"Cache-Control": "no-cache"})

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
