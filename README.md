# Squish

**Every PDF tool. None of your files leave.**

32 PDF tools behind one stateless container. Merge, split, compress, OCR,
convert, watermark, redact — running on hardware you control, with nothing
written to disk after the response and no account to create.

```bash
./run-local.sh                    # venv or docker, with a version check
docker compose up --build         # http://localhost:8000
kubectl apply -f k8s/squish.yaml
```

> **Status: works, not yet proven.** The full test suite is written but has not
> been run end-to-end — see [Tests](#tests) before you rely on this for
> anything that matters.

---

## What it does

| Group | Tools |
|---|---|
| Organize (7) | merge, split (ranges / burst / chunks), split at bookmarks, remove pages, reorder, rotate, N-up |
| Optimize (4) | compress, grayscale, repair, OCR |
| Convert to PDF (2) | image to PDF, Office to PDF |
| Convert from PDF (8) | image, Word, Excel, PowerPoint, PDF/A, Markdown, extract images, extract attachments |
| Edit (6) | watermark, page numbers, header/footer, crop, flatten, metadata |
| Security (5) | protect, unlock, redact, rasterise, compare |

The UI is a single HTML file with no build step, no CDN and no external
requests. The tool grid, the option inputs and the client-side validation are
all generated from `GET /api/tools`.

When you drop several PDFs into a multi-file tool like Merge, each row shows a
**first-page thumbnail** and can be **dragged to reorder** (a keyboard move
button stays for accessibility). Merge also has an **editable output name**,
pre-filled from the input files (`report.pdf` + `invoice.pdf` →
`report+invoice.pdf`).

Single-file page tools (split, remove pages, reorder, rotate, crop, render,
…) show a **visual page picker**: a grid of every page. Click pages to select
them — highlighted to *keep* on most tools, marked in red to *remove* on Remove
Pages — or **drag to reorder** on the Reorder tool. The picker and the page-range
box stay in sync **both ways**: clicking rewrites the box, typing `1-3,7`
re-highlights the grid. The box is never removed — it stays the authoritative
value and the keyboard path. Thumbnails render in the browser with vendored
pdf.js — see [Thumbnails](#thumbnails-pdfjs); no document ever leaves the page.

**Adding a tool is one function plus one registry entry.** Write
`fn(work, inputs, params) -> Result` in `backend/tools.py`, add a `Tool(...)`
beside it, and it appears in the interface with its fields. There is no
frontend change to make.

## How it is put together

iLovePDF runs a **task-lifecycle** API: `POST /start` hands you a task ID and
the hostname of the worker that will hold your file, then you `/upload`,
`/process` and `/download` against that specific worker. That design exists to
support multi-gigabyte batches and cloud-storage imports, and it costs a task
store, sticky routing, and a garbage collector for abandoned uploads.

Squish deliberately does not do that. One request does the whole job:

```
POST /api/t/{tool}   multipart: files[] + options
      -> temp dir -> engine -> stream the result -> delete the temp dir
```

No database, no queue, no object store, no session, nothing on disk after the
response. Any pod can serve any request, scaling is just replicas, and there is
no cleanup job to forget to write.

**The trade:** a job cannot outlive its HTTP connection. `JOB_TIMEOUT`, the pod
`terminationGracePeriodSeconds` and the ingress read timeout must move
together — they are set and documented as a group in `k8s/squish.yaml`.

Engines are delegated to, not reimplemented:

| Job | Engine |
|---|---|
| Page surgery, rendering, redaction, overlays | PyMuPDF |
| Compression, PDF/A, grayscale | Ghostscript |
| Damaged-file recovery | qpdf |
| Office ↔ PDF | LibreOffice headless |
| OCR | ocrmypdf (tesseract) |
| PDF → Word | pdf2docx |

`GET /api/health` reports which of these the running image actually has. Tools
whose engine is absent are greyed out in the UI rather than failing at submit
time, so a slim build degrades honestly.

## API

| Endpoint | Purpose |
|---|---|
| `GET /` | the single-file UI |
| `GET /api/health` | version, limits, engine availability |
| `GET /api/tools` | the registry the UI builds itself from |
| `POST /api/t/{tool}` | multipart `files[]` + option fields → the result file |
| `GET /metrics` | Prometheus exposition format |

```bash
curl -F 'files=@a.pdf' -F 'files=@b.pdf' \
     http://localhost:8000/api/t/merge -o merged.pdf

curl -F 'files=@scan.pdf' -F 'lang=eng' -F 'deskew=1' \
     http://localhost:8000/api/t/ocr -o searchable.pdf
```

## Layout

```
backend/app.py            FastAPI: health, tools, metrics, POST /api/t/{tool}
backend/tools.py          32 tools + registry + budgets + engine wrappers
backend/static/index.html the entire UI, one file, zero external requests
tests/                    generated fixtures, no committed binaries
Dockerfile                multi-stage, warm LibreOffice profile, build args
docker-compose.yml        tmpfs scratch, size-capped
k8s/squish.yaml           8 documents: hardened deployment, HPA, PDB, ingress
.github/workflows/ci.yml  tests, multi-arch build, Trivy, kubeconform
run-local.sh              venv or docker, version-verified
```

## Engines

Six tools shell out to a command-line engine rather than reimplementing it.
**In Docker every engine is already installed** — `docker compose up --build`
gives you all 32 tools with nothing to add.

For a **native** run (`./run-local.sh` without Docker), the engines come from
your `PATH`. A missing one is not an error: `/api/health` reports it and the UI
greys out just those tools with a `needs <engine>` badge. Install the ones you
want and restart; the badges clear on the next health check.

| Tool(s) | Engine | macOS (Homebrew) | Debian / Ubuntu |
|---|---|---|---|
| Compress, Grayscale | Ghostscript | `brew install ghostscript` | `apt install ghostscript` |
| Repair | qpdf | `brew install qpdf` | `apt install qpdf` |
| OCR, PDF to PDF/A | ocrmypdf | `brew install ocrmypdf` | `apt install ocrmypdf` |
| Office to PDF | LibreOffice | `brew install --cask libreoffice` | `apt install libreoffice` |

All four are free and open-source. Everything else — merge, split, rotate,
watermark, redact, the rest — is pure PyMuPDF and needs no external binary.

## Thumbnails (pdf.js)

The file rows render a first-page preview using **pdf.js**, served from
`backend/static/vendor/` — not a CDN, so the page keeps its zero-external-request
guarantee, and the PDF is rendered locally in the browser.

The two library files are not committed (they are large minified blobs). Get
them one of three ways:

- **Docker** — fetched automatically at build time; containers ship with
  thumbnails working.
- **`./run-local.sh`** (native) — fetches them on first run if missing.
- **Manually** — `cd backend/static/vendor && ./fetch-pdfjs.sh`.

If the files are absent, nothing breaks: rows fall back to a document icon.

## Running on Windows

**Use Docker Desktop.** It is the recommended path and the only one that gives
you all 32 tools with no extra setup — every engine is baked into the image,
and the container is what caps memory:

```powershell
docker compose up --build          # http://localhost:8000
```

**WSL2** is the next best option: open an Ubuntu shell and follow the normal
Linux instructions, including `apt install` for the engines. `run-local.sh`
runs there unchanged.

**Native Windows Python** works for the pure-PyMuPDF tools (merge, split,
rotate, watermark, redact, page numbers, and the rest), with two caveats:

- `run-local.sh` is a bash script and will not run in `cmd`/PowerShell. Start
  the server directly:

  ```powershell
  py -m venv .venv
  .venv\Scripts\pip install -r backend\requirements.txt
  cd backend
  ..\.venv\Scripts\uvicorn app:app --host 127.0.0.1 --port 8000
  ```

- The per-subprocess memory ceiling (`RLIMIT_AS` / `prlimit`) does not exist on
  Windows, so the engine tools run without it. This is the main reason Docker
  is preferred — a malformed PDF handed to Ghostscript can spike memory with
  nothing to cap it. The engine binaries themselves (Ghostscript, qpdf,
  LibreOffice) have Windows installers if you want those tools natively.

## Build variants

```bash
docker build --build-arg WITH_OFFICE=0 --build-arg WITH_OCR=0 -t squish:slim .
```

Full image ~1.5 GB (LibreOffice dominates). Slim ~350 MB, losing Office
conversion, OCR and PDF/A.

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `MAX_UPLOAD_MB` | 200 | per file; enforced during multipart parsing |
| `MAX_TOTAL_UPLOAD_MB` | 400 | whole body; refused on `Content-Length`, before parsing |
| `MAX_OUTPUT_MB` | 1024 | backstop; a larger result returns 507 |
| `MAX_FILES` | 40 | per request |
| `MAX_PAGES` | 5000 | documents above this are refused at open |
| `MAX_RENDER_MP` | 4000 | megapixel budget per job; DPI is clamped to fit |
| `MAX_CONCURRENCY` | 4 | semaphore over heavy jobs; raise with memory, never alone |
| `JOB_TIMEOUT` | 900 | seconds; must stay below the pod grace period |
| `SUBPROC_MEM_MB` | 1536 | `RLIMIT_AS` on engine children; 0 disables |
| `SUBPROC_CPU_SEC` | 600 | `RLIMIT_CPU` on engine children; 0 disables |
| `SCRATCH_DIR` | `/tmp` | an emptyDir on Kubernetes |
| `LO_PROFILE_TEMPLATE` | `/opt/lo-profile` | warm LibreOffice profile baked at build |
| `API_KEY` | unset | when set, `/api/*` needs `X-API-Key` (health is exempt) |

`MAX_TOTAL_UPLOAD_MB` cannot be raised alone. The body is spooled by Starlette
and then copied into the scratch dir, so peak scratch use is roughly twice this
value. It moves together with the compose `tmpfs` size, the Kubernetes
`emptyDir` `sizeLimit`, and the ingress `proxy-body-size` annotation — which
must **equal** it, or nginx rejects batches the service would have accepted and
the service never sees the request.

### Why the output limits exist

`MAX_UPLOAD_MB` caps what comes in, which is the obvious dimension and the
wrong one. A 200 MB PDF with 40,000 pages is small on disk and rasterises to
hundreds of gigabytes — and the scratch dir is a tmpfs under compose, so that
is memory, not disk.

`MAX_RENDER_MP` bounds total pixels per job and scales DPI down to fit rather
than refusing the work:

| Job | Requested | Actual |
|---|---|---|
| 3 pages | 150 DPI | 150 DPI |
| 250 pages | 150 DPI | 150 DPI |
| 400 pages | 600 DPI | 321 DPI |
| 5000 pages | 300 DPI | 90 DPI |

Engine subprocesses additionally run under `RLIMIT_AS` and `RLIMIT_CPU` (via
`prlimit` where available), so a malformed PDF that sends Ghostscript into a
memory spiral kills its own child rather than triggering the OOM killer —
which on Kubernetes takes the whole container down, including the other
requests that pod was serving.

## Metrics

`GET /metrics` returns job counts by tool and outcome (`ok`, `rejected`,
`timeout`, `error`, `too_large`), recent p50/p95 duration per tool, and
semaphore saturation.

`squish_inflight` sitting at `squish_concurrency_limit` means requests are
queueing, which the HPA's CPU target will not show you.

This endpoint is **not** behind `API_KEY` — it sits outside `/api` so scrapers
work without credentials. Restrict it at the ingress if the deployment is
public.

## Tests

```bash
pip install -r backend/requirements-dev.txt
pytest
```

103 test functions across three files, several parametrised. Fixture PDFs are
**generated, not committed** — binary fixtures rot, nobody can review a diff on
them, and a corrupt one produces a failure that looks like a code bug.

Assertions check outcomes rather than existence. "Compress produced a file"
passes on a corrupt zero-page PDF; "the output has the same page count and is
no larger" does not.

Tests needing `gs`, `qpdf` or `soffice` skip when the binary is absent, and CI
installs all three then **fails the build if anything skipped for a missing
engine** — otherwise a green board would mean nothing for the half of the suite
that matters most.

**The honest status:** the suite was written in an environment without PyPI
access, so PyMuPDF, FastAPI and ocrmypdf were never installed and the tests
have never executed. Everything verified so far was verified against the CLI
engines directly or by logic check. `pytest` is the real acceptance gate for
this repo — treat any claim about a PyMuPDF-backed tool as unproven until it
passes.

## Two things to know before you deploy this

**Redaction actually destroys content.** `redact` calls `apply_redactions`,
which rewrites the content stream, so the text is gone rather than covered —
not the classic black rectangle with selectable text underneath. Two limits
follow from that: it only finds text in the *text layer*, so a scan must be
OCR'd first, and it matches literal terms. Verify the output before releasing
anything sensitive.

**This assumes trusted users.** There is no rate limiting and no per-IP quota,
and Ghostscript — which has a long CVE history — is handed user-supplied files
by design. The subprocess limits and the hardened pod spec are containment, not
a substitute for putting an authenticated proxy in front of a public
deployment.

## Accessibility

The UI targets WCAG 2.1 AA. Contrast is measured, not eyeballed — three tokens
failed on the first pass and were corrected:

| Token | Was | Now |
|---|---|---|
| `--dim` (dark) | `#6b7192` = 3.69:1 | `#7b8190` = 4.52:1 |
| `--dim` (light) | `#8e93ae` = 3.03:1 | `#585a6b` = 6.80:1 |
| primary button | white on `#7c5cff` = 4.35:1 | `#6b46e0` = 5.87:1 |

Unavailable tools use `aria-disabled` rather than the `disabled` attribute. A
`disabled` button is skipped by keyboard navigation entirely, so a screen
reader user would never learn the tool exists — this way they reach it and hear
why it is off.

Also: emoji icons are `aria-hidden` so they are not announced before every tool
name; the drop zone is a real `<button>`, not a `<div onclick>`; toasts live in
an `aria-live` region with errors as `role="alert"`; focus moves into the tool
panel on open and returns to the originating tile on close; Escape closes the
panel; `prefers-reduced-motion` is honoured.

## Contributing

New tool: write `fn(work, inputs, params)` in `backend/tools.py`, add a
`Tool(...)` entry, add tests, and add it to the `needs` map in `app.py` if it
shells out to a binary. House rules:

- Never `subprocess` with a shell string — always an exec array, always capture
  stderr to the logger.
- The UI stays one file: no CDN, no build step. `localStorage` holds UI
  preferences only, never document data.
- A `select` field's default must appear in its own option list, or the
  dropdown renders blank and posts an empty value. There is a test for this.
- Bump `APP_VERSION` on any behaviour change — the footer and `run-local.sh`
  both surface staleness, and `run-local.sh` hard-fails unless the running
  server reports the expected version.

`ROADMAP.md` buckets planned work by architectural cost rather than by demand:
what fits the current model, what needs a new engine, and what would break
statelessness.

## Licence

**AGPL-3.0.** Squish links PyMuPDF, which is AGPL-3.0, so this is the licence
the dependency requires rather than a preference. In practice: you can
self-host it, modify it, and run it internally, but if you offer a modified
version to users over a network you must publish your changes.

Shipping this inside a closed-source commercial product needs a commercial
PyMuPDF licence from Artifex, or a swap to pikepdf (MPL-2.0) plus pypdfium2
(BSD) — and that swap costs real redaction, because PDFium has no redaction
applier.

Bundled engines carry their own licences: Ghostscript (AGPL), qpdf
(Apache-2.0), LibreOffice (MPL-2.0), tesseract (Apache-2.0).
