# Squish — implementation plan

What is left to build, in the order that makes sense, with the architectural
decisions each item forces.

---

## The one constraint that shapes everything

Squish is sync-streaming: one HTTP request does upload → process → download,
and nothing survives the response. That is why there is no database, no queue,
no cleanup job, and why any pod can serve any request.

Every remaining feature falls into one of three buckets:

| Bucket | Meaning |
|---|---|
| **A — free** | Fits the current model. New tool function, new registry entry, done. |
| **B — new engine** | Fits the model but adds a binary to the image. Cost is image size and a security surface, not architecture. |
| **C — breaks statelessness** | Needs the document to outlive the request. This is a real inflection point and should be delayed as long as possible. |

Sorting the backlog this way matters more than sorting it by user demand,
because bucket C is where a weekend project turns into an operations job.

---

## Phase 1 — bucket A: cheap wins, no architecture change

These are all "write a function, add a `Tool(...)` entry". Nothing else moves.

| Feature | Approach | Effort |
|---|---|---|
| **Extract images** | `page.get_images()` → save originals at native resolution, zip. Distinct from PDF→JPG, which rasterises whole pages. | 0.5 d |
| **Add attachments / extract attachments** | `doc.embfile_add()` / `embfile_get()`. | 0.5 d |
| **Metadata editor** | Read/write `doc.metadata`; also a "strip all metadata" mode, which is the privacy-relevant one. | 0.5 d |
| **Flatten** | Bake annotations and form fields into page content so they cannot be edited or hidden. | 0.5 d |
| **N-up / booklet** | Impose 2 or 4 source pages per sheet via `show_pdf_page` into a grid. Booklet ordering is the fiddly part. | 1 d |
| **Split by bookmark** | Use the outline tree to pick split points instead of typing ranges. Genuinely useful on long reports. | 1 d |
| **Header / footer text** | Generalisation of the existing `page-numbers` tool — same positioning code, arbitrary text with `{n}`, `{total}`, `{date}`, `{filename}`. | 0.5 d |
| **Grayscale / ink-saver** | Ghostscript `-sColorConversionStrategy=Gray`. One-line tool, meaningful file-size win. | 0.5 d |
| **Rasterise (flatten to images)** | Render every page and rebuild as image-only PDF. Blunt but effective sanitiser for untrusted PDFs. | 0.5 d |

**Total ≈ 5–6 days for 9 tools.** This is the best value in the whole plan and
I would do all of it before touching anything below.

---

## Phase 2 — bucket B: new engines

Each of these adds weight to the image and, in one case, a real security
problem.

### HTML / URL → PDF (~1.5 d, +400 MB)

Playwright with headless Chromium. `wkhtmltopdf` (what iLovePDF uses) is
effectively unmaintained and renders modern CSS badly — don't inherit that
choice just because they made it.

**This is the one feature that introduces a server-side request forgery hole.**
A user-supplied URL fetched by your pod can reach `169.254.169.254` (cloud
metadata, i.e. your credentials), `10.0.0.0/8`, and anything else inside the
cluster. Non-negotiable mitigations before this ships:

- resolve the hostname first, reject any address in a private/link-local/loopback range, then connect **to the resolved IP** — resolving twice invites a DNS-rebinding bypass
- block non-`http(s)` schemes outright
- run Chromium with `--no-sandbox` only if the pod already has `seccompProfile: RuntimeDefault`, and give this workload its own NetworkPolicy with egress restricted to the public internet
- cap render time and page count

If that is more than you want to own, offer "upload an `.html` file" instead
and skip URL fetching entirely. Same feature, none of the risk.

### Scan → PDF (~2 d, no server change)

Camera capture in the browser (`getUserMedia`), client-side edge detection and
perspective correction, then post the corrected images to the existing
`jpg-to-pdf` endpoint. All the work is UI; the backend already supports it.

### Better PDF → Excel (~1–2 d)

`find_tables()` handles ruled tables well and whitespace-aligned tables poorly.
Add Camelot or a lattice/stream toggle exposed as a field, so the user can pick
the strategy when the default misses. Manage expectations in the blurb — this
is table *extraction*, not layout reconstruction, and it will never be perfect.

### Digital signatures (~3–5 d) — read this before promising it

Two completely different things share the word "sign":

1. **A drawn signature image** stamped onto the page. Cosmetic. Legally about
   as strong as a photo of your handwriting. Roughly 1 day, and it is really an
   image-overlay tool.
2. **A cryptographic signature** (PAdES / PKCS#7) that binds a certificate to
   the byte range of the document and detects tampering. This is what
   "signed PDF" means in a regulated context.

PyMuPDF cannot do (2). You need `pyHanko`, plus a real certificate, plus a
decision about where the private key lives — and "in the pod" is a bad answer,
which pushes you toward a KMS or HSM integration.

**Recommendation:** ship (1), label it honestly in the UI as a *visual*
signature, and treat (2) as its own project rather than a tool in the grid.

---

## Phase 3 — bucket C: the statefulness inflection

Everything below needs a document to persist between requests. Do not do any of
it until Phase 1 is done and you actually want these features.

### The minimum viable break

When you need it, add exactly this and no more:

- object storage (MinIO locally, S3 in cluster) holding uploads under a random
  opaque ID, with a **bucket lifecycle rule set to 2 hours** so expiry is the
  storage layer's job, not a cron script you have to write and monitor
- a `POST /api/session` that returns `{id, expires_at}`
- tools gain an optional `session_id` input instead of a file upload

That is one dependency. It is not Redis, Celery, Postgres and a worker fleet —
resist that, because each one is a thing that can be down at 3am.

### What it unlocks

| Feature | Notes | Effort (after the break) |
|---|---|---|
| **Tool chaining / workflows** | "Compress → watermark → protect" as a saved pipeline. This is iLovePDF's actual premium differentiator and the strongest reason to take on state. | 3–4 d |
| **Visual PDF editor** | See below. | 10–15 d |
| **PDF forms** | Field detection is easy (`widget` objects); building a form *designer* is not. | 5–8 d |
| **Large-file / resumable upload** | Chunked upload with `Content-Range`. Only worth it above ~200 MB. | 2–3 d |
| **Async jobs + webhooks** | Needed if any single job routinely exceeds ~5 minutes. | 3 d |

### On the visual editor specifically

This one feature is larger than every other item on this page combined. It is
`pdf.js` for rendering, a canvas overlay for editing, an operation model that
survives a page reload, and — the part everyone underestimates — reconciling
edits against a PDF's actual content streams. Editing existing text in a PDF is
genuinely hard, because glyphs are positioned individually and the font may be
subsetted with no usable character map.

Be clear about which you are building:

- **annotation layer** — add text boxes, shapes, images, highlights *on top*.
  Tractable, ~5 days, covers most real use.
- **true text editing** — reflowing existing paragraphs. Months, and commercial
  products still do it imperfectly.

Ship the annotation layer. Call it "Annotate", not "Edit", so nobody arrives
expecting Word.

---

## AI features — decided: no

iLovePDF has Summarize, Translate and Chat-with-PDF. All three require sending
document content to a language model, and **that is a line Squish does not
cross.** The product's one claim is "None of your files leave," and it is the
exact thing iLovePDF cannot say. A summarise button that posts your document to
an external API would make the claim false, so it is out — not deferred, not
gated behind a flag, out.

If a future user genuinely needs it, the only acceptable shape is a fully local
model (an Ollama sidecar) so nothing leaves the box, and it would ship disabled
by default. Even then it is a distant maybe, not a plan. Do not add an
external-API AI feature to this codebase.

---

## UI track — runs parallel to all of the above

The current UI is functional and honest, but it has four real weaknesses. Item
1 is the highest-value change in this entire document.

### 1. Visual page picker (~3 d) — do this first

Ten tools currently ask the user to type `1-3,7,10-` into a text box. That
requires knowing what is on page 7 of a document they may have just received.

Replace it with `pdf.js` thumbnails rendered **in the browser**: click to
select, drag to reorder, per-page rotate and delete badges. The component
replaces the `pages` field everywhere at once, since the field is already
declared centrally in the registry.

This is also free privacy theatre that happens to be true — the document is
rendered client-side, so page selection never touches the server.

### 2. Accessibility (~2 d) — currently would fail an audit

Owning the specific problems in what I shipped:

- emoji used as tool icons with no `aria-hidden`, so screen readers announce
  "briefcase" before every tool name
- the drop zone is a `<div>` with an `onclick` — not focusable, not operable by
  keyboard, no role
- disabled tool buttons convey "needs ocrmypdf" only visually
- no focus management when the tool panel opens, and no focus return on back
- toasts are not in an `aria-live` region, so errors are silent to a screen
  reader
- `--dim` (#6b7192) on `--panel` (#151827) is about 3.4:1 — below the 4.5:1
  WCAG AA threshold for body text

None of these are hard. All of them are invisible until someone is blocked.

### 3. Progress and result handling (~2 d)

- swap `fetch` for `XMLHttpRequest` on submit to get **real upload progress**;
  the current bar is an indeterminate animation that tells the user nothing
- render a thumbnail of the result before download
- "Send result to another tool" — the UI half of chaining, and it works
  client-side with a `File` object even before the Phase 3 backend exists
- keep a session-local history of results so a mis-click doesn't lose work

### 4. Batch mode (~2 d)

Accept N files on single-file tools, run them in sequence, return a zip. The
backend already loops; this is mostly UI plus a `zip_dir` call. Compress and
Office→PDF are the obvious candidates.

### Smaller, worth doing

- drag-and-drop reordering for merge (the current ↕ button is a placeholder)
- remember recent tools in `localStorage`
- `?tool=compress` deep links, so the tools are individually shareable
- i18n scaffolding — iLovePDF ships 26 languages, and the string extraction is
  much cheaper to do now than after another 15 tools exist
- a `prefers-reduced-motion` guard on the animations

---

## Suggested sequence

1. **`docker compose up` and exercise every existing tool.** Nothing on this
   page matters until the untested PyMuPDF paths are confirmed working.
2. **Phase 1 (~6 d).** Nine tools, zero architectural risk.
3. **Visual page picker (~3 d).** Biggest single UX improvement available.
4. **Accessibility pass (~2 d).** Cheap, and the longer it waits the more
   surface it has to cover.
5. **Progress + batch + chaining UI (~4 d).**
6. **Phase 2 engines**, as demand dictates. HTML→PDF only with the SSRF
   mitigations written first, not after.
7. **Phase 3** only when tool chaining or annotation is genuinely wanted. Take
   the minimum break: object storage plus a session ID, nothing more.

AI tools are deliberately absent from this sequence — see "AI features" above.

Phases 1 through 5 are roughly three weeks and get you to real parity on
everything except the visual editor, forms and AI.

---

## Open decisions

These need your answer, not mine:

1. **PyMuPDF's AGPL licence** — internal use, open source, commercial licence,
   or swap engines? The swap costs real redaction, so this shapes the security
   story. (Repo currently ships AGPL-3.0.)
2. **Is this multi-tenant or personal?** Everything above assumes trusted
   users. Public exposure adds rate limiting, per-IP quotas, abuse handling,
   and makes PDF-parser hardening a priority rather than a nicety — malicious
   PDFs are a real attack vector against Ghostscript in particular, which has a
   long CVE history.
3. **Does anyone need the API without the UI?** If yes, version the endpoints
   as `/api/v1/` now, while it costs nothing.
