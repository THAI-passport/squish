# Squish — Product & Architecture Specification

> **"Every PDF tool. None of your files leave."**

---

## 1. Executive Summary & Utility

**Squish** is a privacy-first, zero-data-retention PDF processing suite. It provides 39 specialized tools covering organization, optimization, document conversion, visual editing, redaction, cryptographic signing, and secure email dispatch.

### Why Squish Exists (The Core Problem) 
Mainstream online PDF services (iLovePDF, Smallpdf, Adobe Acrobat Web) require uploading sensitive documents—such as employment contracts, financial audits, medical records, and legal filings—to remote third-party cloud servers. These services introduce:
- **Data Privacy Risks**: Files reside on external servers, object storage buckets, and temporary database queues.
- **Monetization Barriers**: Artificial file-size limits, daily operation caps, forced watermarks, and mandatory account registrations.
- **Operational Complexity**: Multi-step task lifecycles (`/start` → `/upload` → `/process` → `/download`) that depend on background workers and cleanup crons.

### The Squish Solution
- **Strict Statelessness**: Every job processes within a per-request temporary scratch directory or in-memory buffer. Once the HTTP response streams back to the user, temporary scratch files are permanently deleted.
- **Hardware Sovereignty**: Runs on hardware you control (Docker, Kubernetes, local native execution) or entirely client-side via browser WebAssembly (WASM).
- **Zero External Telemetry**: The UI is a single static file with zero external requests, no external CDNs, no tracking pixels, and no analytics.
- **Honest Degradation**: Dynamically checks host engine availability and badges missing command-line binaries in the UI instead of failing at run time.

---

## 2. Architecture & Execution Modes

Squish operates under two primary execution models:

```
                          ┌─────────────────────────────┐
                          │   Squish UI (Single File)   │
                          │   Zero-CDN, Pure HTML/JS    │
                          └──────────────┬──────────────┘
                                         │
                 ┌───────────────────────┴───────────────────────┐
                 ▼                                               ▼
   ┌───────────────────────────┐                   ┌───────────────────────────┐
   │   Native / Container Mode │                   │  Static / WASM Cloudflare │
   │   (FastAPI Backend)       │                   │  (Pyodide in Browser Tab) │
   ├───────────────────────────┤                   ├───────────────────────────┤
   │ • PyMuPDF (Core Surgery)  │                   │ • PyMuPDF in WebAssembly  │
   │ • Ghostscript (Compress)  │                   │ • Browser Crypto Vault    │
   │ • LibreOffice (Office)    │                   │ • Client-side Encryption  │
   │ • OCRmyPDF (Tesseract)    │                   │ • Cloudflare Worker for   │
   │ • qpdf (Damaged Repair)   │                   │   raw TCP SMTP dispatch   │
   │ • pyHanko (PKCS#12 Sign)  │                   │ • Zero data leaves tab    │
   │ • pdf2docx (Word Export)  │                   │                           │
   └───────────────────────────┘                   └───────────────────────────┘
```

### Mode A: Container / Native Server (FastAPI)
- **Lifecycle**: `POST /api/t/{tool}` accepts a multipart payload → writes to a unique scratch directory in a `tmpfs` mount → delegates to native engines → streams the result via chunked `FileResponse` → triggers immediate `shutil.rmtree()` cleanup via `BackgroundTask`.
- **Resource Ceilings**:
  - `MAX_UPLOAD_MB`: Per-file upload ceiling (default: 200 MB).
  - `MAX_TOTAL_UPLOAD_MB`: Total multipart body ceiling (default: 400 MB).
  - `MAX_PAGES`: Maximum document page limit (default: 5,000 pages).
  - `MAX_RENDER_MP`: Output render ceiling in megapixels to prevent memory exhaustion (default: 4,000 MP).
  - `SUBPROC_MEM_MB` & `SUBPROC_CPU_SEC`: Out-of-process engine resource sandboxing via `prlimit` or POSIX `setrlimit`.
  - `MAX_CONCURRENCY`: Global asyncio semaphore to prevent CPU/memory thrashing on heavy jobs.

### Mode B: Static WebAssembly / Cloudflare Pages
- **Browser-Only Execution**: Python tools run in the client’s browser tab via Pyodide WebAssembly.
- **Client-Side Encryption**: PDFs are encrypted with AES-256 directly inside the browser using Web Crypto before any network dispatch occurs.
- **Cloudflare Worker Dispatch**: When using Secure Email Dispatch, the worker receives only the pre-encrypted PDF and one-time SMTP credentials, proxying outbound SMTP over raw TCP sockets (`cloudflare:sockets`) without saving or caching data.

---

## 3. Complete Feature Catalog (39 Tools)

Squish organizes its 39 tools into 6 dedicated categories:

| Group | Tools Count | Key Capabilities |
|---|:---:|---|
| **Organize** | 7 | Merge, Split, Remove Pages, Reorder, Rotate, Split at Bookmarks, N-up Booklet |
| **Optimize** | 4 | Compress, Repair, Grayscale, OCR Text Recognition |
| **Convert to PDF** | 3 | Image to PDF, Office to PDF, Markdown to PDF |
| **Convert from PDF** | 10 | PDF to Image, Word, Excel, PowerPoint, PDF/A, Markdown, Text, Extract Images, Attachments, Fonts |
| **Edit** | 6 | Watermark, Page Numbers, Crop, Header / Footer, Flatten, Edit Metadata |
| **Security** | 9 | Rasterise, Protect, Unlock, Redact, Auto-Redact, Sign PDF, Verify Signature, Compare, Secure Email Dispatch |

---

### 3.1. Organize (7 Tools)
1. **Merge PDF (`merge`)**
   - Combines multiple PDF files into a single unified document in custom order.
   - Live first-page thumbnail generation and drag-and-drop file reordering.
   - Auto-generates clean output filenames derived from input stems (e.g., `docA+docB.pdf`).
2. **Split PDF (`split`)**
   - Supports 3 extraction modes:
     - `ranges`: Extract specific page sets (e.g. `1-3, 5, 8-`) into a single PDF.
     - `every`: Burst the document into individual 1-page PDFs packaged in a `.zip`.
     - `chunks`: Split the document into fixed-size page groups (e.g. 5 pages per file).
3. **Remove Pages (`remove-pages`)**
   - Deletes specified pages (e.g., `2, 4-6`) while preserving the rest of the document structure, outlines, and annotations.
4. **Reorder Pages (`organize`)**
   - Reconstructs document order based on an explicit page sequence (e.g., `3, 1, 2, 4-`) with visual drag-and-drop page tile rearrangement.
5. **Rotate PDF (`rotate`)**
   - Rotates all or targeted pages by 90°, 180°, or 270° clockwise/counter-clockwise.
6. **Split at Bookmarks (`split-bookmarks`)**
   - Parses the internal PDF Table of Contents / Outline tree and splits large reports into separate titled section documents at chosen hierarchy levels (Level 1, 2, or 3).
7. **N-up / Booklet (`n-up`)**
   - Imposes 2 or 4 pages per sheet onto landscape or portrait canvases with customizable point margins to save paper or create print-ready booklets.

---

### 3.2. Optimize (4 Tools)
8. **Compress PDF (`compress`)**
   - Re-compresses raster images and deduplicates internal structural objects.
   - Offers 3 presets: *Low* (high fidelity), *Recommended* (balanced), and *Extreme* (maximum reduction).
9. **Repair PDF (`repair`)**
   - Reconstructs corrupted xref (cross-reference) tables, recovers readable streams from damaged files, and repairs invalid trailer structures using `qpdf` and PyMuPDF.
10. **Grayscale PDF (`grayscale`)**
    - Strips color spaces (converting `DeviceRGB` and `DeviceCMYK` to `DeviceGray`) via Ghostscript or browser-side image conversion to minimize ink costs and file sizes.
11. **OCR PDF (`ocr`)**
    - Performs Optical Character Recognition using OCRmyPDF and Tesseract.
    - Generates a selectable, searchable invisible text layer over scanned documents.
    - Supports multiple languages (English, French, German, Spanish, Portuguese, Italian), automated deskewing of crooked scans, and forced re-OCR options.

---

### 3.3. Convert to PDF (3 Tools)
12. **Image to PDF (`jpg-to-pdf`)**
    - Converts single or multiple images (`.jpg`, `.jpeg`, `.png`, `.webp`, `.bmp`, `.tif`, `.tiff`) into a PDF.
    - Configurable sizing modes (*Fit to image*, *A4*, *US Letter*) and point margin controls.
13. **Office to PDF (`office-to-pdf`)**
    - Headless LibreOffice conversion supporting `.docx`, `.doc`, `.xlsx`, `.xls`, `.pptx`, `.ppt`, `.odt`, `.ods`, `.odp`, `.rtf`, `.txt`, and `.csv`.
14. **Markdown to PDF (`md-to-pdf`)**
    - High-fidelity print rendering powered by WeasyPrint (or client-side WASM engine).
    - Full support for GitHub-flavored Markdown: fenced code syntax highlighting, responsive tables, footnotes, definition lists, and page-break rules.
    - **SSRF Hardening**: Rejects local `file://` schemes and blocks private, loopback, and link-local IP addresses when remote image fetching is enabled.

---

### 3.4. Convert from PDF (10 Tools)
15. **PDF to Image (`pdf-to-jpg`)**
    - Renders pages to JPG or PNG formats at user-selected resolutions (36 to 600 DPI) with page-range filtering.
16. **PDF to Word (`pdf-to-word`)**
    - Reconstructs text flow, paragraph boundaries, font weights, and embedded tables into an editable Microsoft Word `.docx` file using `pdf2docx`.
17. **PDF to Excel (`pdf-to-excel`)**
    - Detects tabular grid boundaries across selected pages and exports each table into a dedicated `.xlsx` worksheet.
18. **PDF to PowerPoint (`pdf-to-powerpoint`)**
    - Renders PDF pages into full-bleed presentation slides within a `.pptx` presentation.
19. **PDF to PDF/A (`pdf-to-pdfa`)**
    - Converts standard PDFs into ISO-compliant PDF/A-2b archival format for long-term legal and compliance preservation.
20. **PDF to Markdown (`pdf-to-markdown`)**
    - Extracts the textual content into clean, structured Markdown page by page.
21. **PDF to Text (`pdf-to-text`)**
    - Extracts raw plain text layer into a `.txt` file.
22. **Extract Images (`extract-images`)**
    - Recovers embedded raster image files at their native resolution without re-compression, packaging them in a `.zip` archive. Includes a minimum-dimension pixel filter to discard UI rules, bullet points, and spacers.
23. **Extract Attachments (`extract-attachments`)**
    - Extracts embedded non-PDF file attachments stored inside the PDF catalogue.
24. **Extract Fonts (`extract-fonts`)**
    - Identifies and extracts embedded font programs (`.ttf`, `.otf`, `.cff`) from the document.

---

### 3.5. Edit (6 Tools)
25. **Add Watermark (`watermark`)**
    - Applies single-stamp or repeating tiled text watermarks.
    - Configurable text, 9-point positional alignment, rotation angles (-180° to +180°), opacity (5% to 100%), font size, and color.
26. **Add Page Numbers (`page-numbers`)**
    - Injects dynamic page number sequences using templates like `Page {n} of {total}`.
    - Configurable starting index, positioning, font size, and color.
27. **Crop PDF (`crop`)**
    - Trims page bounding boxes by specifying top, bottom, left, and right margins in points.
28. **Header / Footer (`header-footer`)**
    - Injects running header or footer lines with variable substitution tokens: `{n}` (page number), `{total}` (page count), `{date}` (current ISO date), and `{filename}` (source file stem).
29. **Flatten PDF (`flatten`)**
    - Permanently bakes interactive form fields, checkboxes, dropdowns, and annotations into the underlying static graphics stream to prevent modification.
30. **Edit Metadata (`metadata`)**
    - Modifies document properties (`Title`, `Author`, `Subject`, `Keywords`, `Creator`, `Producer`).
    - Includes a **Deep Metadata Strip** mode that purges both the legacy Info dictionary and the XMP XML metadata stream.

---

### 3.6. Security (9 Tools & Subsystems)
31. **Rasterise PDF (`rasterise`)**
    - Flattens all pages into full-page bitmap images at selected DPI. Destroys hidden layers, active JavaScript, embedded macros, form fields, and malicious exploits from untrusted documents.
32. **Protect PDF (`protect`)**
    - Encrypts documents with AES-256 standard encryption.
    - Supports granular permission flags: disallow text copying, disallow annotation/editing, and require master password for modifications.
33. **Unlock PDF (`unlock`)**
    - Decrypts password-protected PDFs and exports an unencrypted copy when provided with the valid password.
34. **Redact PDF (`redact`)**
    - Performs true surgical content redaction: locates search terms, deletes underlying text glyphs from the content stream, and draws visual redaction blocks.
35. **Auto-Redact (`auto-redact`)**
    - Automatically scans and redacts sensitive PII patterns including:
      - Email Addresses
      - US Social Security Numbers (SSN)
      - International & US Phone Numbers
      - Credit Card Numbers
36. **Sign PDF (`sign-pdf`)**
    - Applies cryptographic PAdES / PKCS#7 digital signatures using personal PKCS#12 certificates (`.p12` or `.pfx`) via `pyHanko`.
37. **Verify Signature (`verify-signature`)**
    - Inspects digital signatures on a signed PDF to report cryptographic validity, document tampering/modification levels, and signer certificate details.
38. **Compare PDFs (`compare`)**
    - Performs visual and textual line-by-line diff comparisons between two revisions of a document.
39. **Secure Email Dispatch (`email-secure`)**
    - An end-to-end confidential document transmission system combining encryption, dual-message delivery, and out-of-band key verification.

---

## 4. Secure Email Dispatch & Web Crypto Vault

Squish features an enterprise-grade confidential document dispatch workflow:

```
                               ┌───────────────────────────┐
                               │     Master PDF Document   │
                               └─────────────┬─────────────┘
                                             │
                       ┌─────────────────────┴─────────────────────┐
                       │ 1. AES-256 Encryption (Random/Manual Key) │
                       │ 2. Optional PKCS#12 Digital Signature     │
                       │ 3. Optional Page Bursting / Splitting     │
                       └─────────────────────┬─────────────────────┘
                                             │
                      ┌──────────────────────┴──────────────────────┐
                      ▼                                             ▼
          ┌───────────────────────┐                     ┌───────────────────────┐
          │  Email #1: Document   │                     │  Email #2 / OOB Key   │
          ├───────────────────────┤                     ├───────────────────────┤
          │ • Encrypted PDF file  │                     │ • Decryption Password │
          │ • Sanitized Template  │  (Delayed delivery  │ • Out-of-Band Sharing │
          │ • Zero Password Data  │   or OOB Channels)  │   (QR / WhatsApp/SMS) │
          └───────────────────────┘                     └───────────────────────┘
```

### Key Capabilities of Secure Dispatch
- **Dual-Message Transmission**: Email #1 contains the encrypted PDF and contextual note. Email #2 delivers the decryption password after an intentional delay (to prevent simultaneous inbox interception).
- **Out-of-Band (OOB) Sharing**: Generate one-click QR codes, WhatsApp share links, or SMS links to transmit the decryption key over a separate channel.
- **Page Bursting & Filtering**: Dispatch specific pages from a master document to individual recipients without creating persistent intermediate files.
- **Bulk Personalization**: Supports JSON, CSV, and TSV recipient imports with variable substitution (`{{name}}`, `{{email}}`, `{{message}}`, `{{doc_name}}`, `{{password}}`).
- **Template Safety**: Rejects Email #1 templates containing `{{password}}` to prevent accidental key leaks in the document delivery email.

### Client-Side Encrypted Vault (`vault.js`)
- **Zero Server Trust**: In local mode, credentials never touch server disk.
- **PBKDF2 + AES-256-GCM**: The vault is encrypted in `localStorage` with a master PIN using 100,000 PBKDF2 iterations (300,000 for export backups) and 256-bit AES-GCM.
- **Profile Management**: Supports up to 5 masked SMTP profiles (Gmail, Outlook, custom SMTP servers) and up to 20 custom HTML email templates.
- **Portable Backups**: Export and import encrypted `.squishvault` backup files across devices.
- **Instant Wipe**: Single-click "Wipe All" zeroes decrypted memory buffers and purges browser storage.

---

## 5. UI/UX Architecture

- **Zero External Requests**: All CSS, SVGs, and JavaScript are bundled directly into `backend/static/index.html`. No Google Fonts, external CDN scripts, or remote assets.
- **Interactive Page Picker (PDF.js)**:
  - Local browser-side page preview generation via vendored PDF.js.
  - Interactive grid: click pages to toggle selection, drag-and-drop to reorder.
  - Real-time **bidirectional synchronization**: typing `1-3, 5` updates the visual grid; clicking tiles updates the range input.
- **Command Palette (`Cmd + K` / `/`)**: Instant keyboard search across all 39 tools with arrow-key navigation and quick actions.
- **Design System**:
  - Warm neutral theme palette (Ink & Cobalt) with WCAG-AA compliant contrast ratios.
  - Smooth light and dark mode transitions respecting `prefers-color-scheme`.
  - Accessible focus rings, screen-reader status regions (`aria-live`), and reduced-motion fallbacks.
- **PWA & Offline Mode**: Registered service worker (`sw.js`) and manifest enabling offline installation and execution in WebAssembly mode.

---

## 6. API Reference & Engine Matrix

### REST Endpoints
| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Serves the single-page web UI |
| `GET` | `/api/health` | Service version, active resource limits, and engine availability |
| `GET` | `/api/tools` | Dynamic tool registry definitions used to render the UI |
| `GET` | `/api/runtime` | Current security and vault runtime model (local vs cloud) |
| `POST` | `/api/t/{tool}` | Multipart processing endpoint (`files[]` + tool parameters) |
| `POST` | `/api/smtp/test` | Verifies SMTP credentials without sending an email |
| `POST` | `/api/smtp/resend-key` | Resends Email #2 decryption key in case of delivery retries |
| `GET` | `/metrics` | Prometheus metrics (job counts, durations, inflight concurrency) |

### Underlying Engine Delegation Matrix
| Purpose | Primary Engine | Fallback / Static Mode |
|---|---|---|
| Page manipulation, rendering, redaction | **PyMuPDF (`fitz`)** | PyMuPDF in Pyodide WASM |
| Compression & Grayscale | **Ghostscript (`gs`)** | PyMuPDF WASM rasterization |
| Damaged PDF Recovery | **qpdf** | PyMuPDF stream repair |
| Office Document Conversion | **LibreOffice (headless)** | Server container required |
| Optical Character Recognition | **OCRmyPDF / Tesseract** | Server container required |
| PDF to Microsoft Word | **pdf2docx** | Server container required |
| Markdown to PDF | **WeasyPrint** | Browser Markdown renderer |
| Cryptographic PKCS#12 Signing | **pyHanko** | Server container required |
