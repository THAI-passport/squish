---
name: squish-wasm-limitations
description: Limitations, gotchas and rules for Squish's static/WebAssembly (Pyodide) client-side mode — the browser-only build that runs on Cloudflare with no backend. Consult this before adding a tool, touching generate_client_tools.py, or debugging a "Local processing failed" error in static mode.
type: reference
---

# Squish — Static / WASM mode limitations

Squish normally runs as a FastAPI backend. This document covers the **static
(WASM) mode**: the same single-page UI served as static files (e.g. Cloudflare
Pages/Workers) with **no backend at all**. PDF work happens in the browser via
**Pyodide** (CPython compiled to WebAssembly) running the unmodified
`tools.py`, with **PyMuPDF** as the wasm engine.

Privacy is stronger here than the backend build, not weaker: with no server,
uploaded files never leave the tab. Only *code* (the Pyodide runtime and Python
wheels) is fetched over the network.

## How it works

```
index.html boot()
  ├─ fetch('/api/tools')  (1.5s timeout)
  │     ├─ ok  → backend mode  (POST /api/t/{tool})
  │     └─ fail/timeout → STATIC MODE:
  │            load client_tools.js  →  window.onStaticToolsReady(STATIC_TOOLS)
  │
  └─ submit(): isStatic ? window.runPyodideTool(key, files, fd)
                        : fetch('/api/t/'+key, …)
```

`client_tools.js` is **generated** by `backend/generate_client_tools.py`. It is
not hand-edited. It embeds:

- `STATIC_TOOLS` — the tool registry as JSON, with a per-tool `available` flag.
- `STATIC_VERSION` — the real build version, scraped from `app.py`.
- `TOOLS_PY_SOURCE` — the entire `tools.py` as a string, written into Pyodide's
  virtual filesystem and imported.
- The Pyodide loader + `run_tool_wrapper(key, files_json, params_json)` glue.

On first run it loads Pyodide from the jsDelivr CDN, `micropip.install`s the
Python packages, writes `tools.py`, and imports it. Each tool run writes the
input files into the in-memory FS under `/tmp/squish_work`, calls the tool's
`fn(work, inputs, p)`, reads the result bytes back into a Blob, and deletes the
scratch dir.

## The hard rule when adding or changing a tool

**Every tool that cannot run in the browser MUST be listed in the `unsupported`
map in `generate_client_tools.py`.** If it is not, the UI shows it as a normal,
clickable tool and it throws *"Local processing failed: …"* only when the user
runs it. The blocklist is the single source of truth for browser availability —
there is no runtime capability probe.

A tool cannot run in the browser if either:

1. **It shells out to a native binary.** There is no `subprocess` in Pyodide.
   Anything calling `run([...])` (Ghostscript `gs`, `qpdf`, LibreOffice
   `soffice`, `ocrmypdf`) is out.
2. **It imports a Python package with no usable wasm build.** WeasyPrint (native
   cairo/pango) and pyhanko (crypto stack) cannot run under Pyodide.

Pure-Python packages *can* be enabled: add them to the `micropip.install` list
(the optional, best-effort call) instead of blocklisting the tool.

## Tool availability matrix (build 1.5.0-squish, 39 tools)

**Blocked in static mode** (in the `unsupported` map):

| Tool | Reason |
|---|---|
| pdf-to-pdfa | Ghostscript (`gs`) |
| office-to-pdf | LibreOffice (`soffice`) |
| ocr | ocrmypdf + tesseract |
| pdf-to-word | pdf2docx (native deps) |
| sign-pdf | pyhanko (cannot run in wasm) |
| verify-signature | pyhanko (cannot run in wasm) |

**Browser-specific PyMuPDF fallbacks:**

| Tool | Browser behaviour |
|---|---|
| compress | Structural cleanup, deduplication and deflate; no Ghostscript image downsampling |
| repair | Best-effort PyMuPDF xref recovery; qpdf remains stronger on the server |
| grayscale | Rebuilds pages as grayscale images, so searchable text is rasterised |
| md-to-pdf | Uses PyMuPDF Story instead of WeasyPrint/Pango |

**Cloudflare Pages Worker:** Secure Email Dispatch is capability-probed at
runtime. It remains disabled on an ordinary static host. When `_worker.js` is
deployed on Cloudflare Pages, the browser encrypts the PDF first and the Worker
sends the already-encrypted attachment and its key via the user's one-time SMTP
credentials. Cloudflare blocks SMTP port 25; use 465 or 587.

**Enabled via optional pure-Python packages** (best-effort `micropip.install`):

| Tool | Package |
|---|---|
| pdf-to-excel | openpyxl |
| pdf-to-powerpoint | python-pptx |

These packages load lazily only when their corresponding export tool is used,
so they never delay or break the core engine. If an optional install fails
(offline or package index blocked), only that export errors per-run. They are
the least browser-verified path — smoke-test them in a real browser after any
change.

**Everything else runs on PyMuPDF** (merge, split, remove/organize/rotate pages,
split-bookmarks, n-up, jpg-to-pdf, pdf-to-jpg, pdf-to-markdown, pdf-to-text,
extract images/attachments/fonts, watermark, page-numbers, crop, header-footer,
flatten, metadata, rasterise, protect, unlock, redact, auto-redact, compare).

## Fixed gotchas — do not reintroduce

1. **Incomplete blocklist.** The first port only blocked the six CLI-binary
   tools and missed grayscale (gs), md-to-pdf (WeasyPrint), pdf-to-excel
   (openpyxl), pdf-to-powerpoint (python-pptx), and sign/verify (pyhanko). All
   five showed as available and crashed at run time. Keep the `unsupported` map
   in sync with `tools.py`.
2. **Wrong env-var name silently disabled the render budget.** The generator set
   `MAX_MEGAPIXELS`, but `tools.py` reads `MAX_RENDER_MP`. The override did
   nothing and the budget stayed at the 4000 MP *server* default, which can OOM
   the tab on `pdf-to-jpg` / `rasterise` / `pdf-to-powerpoint`. It is now
   `MAX_RENDER_MP=300` (and `MAX_PAGES=100`). These are set **before**
   `import tools`, because tools.py reads them into module-level constants at
   import time — ordering matters.
3. **Engine install must not be all-or-nothing.** Core packages (`pymupdf`,
   `markdown`, `pygments`) are installed in a must-succeed try/catch that
   surfaces a visible error and stops. The optional export packages are a
   *separate* best-effort install so a failure there cannot take down every
   tool.
4. **Stale hardcoded version.** The badge said `v1.0 (static mode)` regardless
   of the real build. It now shows `STATIC_VERSION`, scraped from `app.py` at
   generation time and applied in `onStaticToolsReady`.
5. **Toasts lost their a11y.** A regressed `toast()` built an ad-hoc `#toast`
   div with hardcoded colors, bypassing the themed `#toasts` region and the
   `role="alert"` / `role="status"` announcement. Restored to append into
   `#toasts` with the right role.

6. **CDN / offline dependency removed.** Pyodide and the core wheels (PyMuPDF,
   Markdown and Pygments) are vendored into `static/vendor/pyodide/`. The
   service worker caches them, making the core PDF tools fully offline-capable.

## Known remaining limitations (not yet addressed)

- **First-run cost & memory.** Big documents are bounded by `MAX_PAGES=100` and
  `MAX_RENDER_MP=300`; tune in the generator, not in `tools.py`.
- **Backend detection is a 1.5 s timeout.** A real but slow backend can be
  misdetected as static. Fine for a static-only deployment.
- **Not test-covered.** `pytest` targets the FastAPI backend and does not
  exercise the Pyodide path; the CDN + wasm behaviour is only verifiable in a
  real browser.

## Regeneration

After editing `generate_client_tools.py` (or `tools.py`, or `app.py`'s
version), regenerate:

```
cd backend && python3 generate_client_tools.py   # needs pymupdf importable
```

It rewrites `backend/static/client_tools.js`. Syntax-check with
`node --check backend/static/client_tools.js`. Never edit `client_tools.js`
by hand — it is a build artifact.
