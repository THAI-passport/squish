# Third-Party Notices

Squish is distributed under the **GNU Affero General Public License v3.0
(AGPL-3.0)**. That choice is not arbitrary: two components it depends on —
**PyMuPDF** and **Ghostscript** — are themselves AGPL-3.0, so the combined,
distributed work must be AGPL-3.0 as well. Everything else below is either
permissive (MIT / BSD / Apache-2.0) or weak-copyleft (MPL-2.0 / LGPL-2.1) and is
compatible with that license.

This file lists the third-party software Squish uses and their licenses, to
satisfy the attribution and notice requirements of those licenses. Licenses were
recorded as of 2026-07 and reflect the versions pinned in `requirements.txt` and
installed by the `Dockerfile`; the authoritative text is always the one shipped
with each project.

If you intend to ship Squish inside a **closed-source commercial product**, note
that PyMuPDF/MuPDF and Ghostscript require a commercial license from Artifex in
that case (or a swap to permissively-licensed engines — see `ROADMAP.md`).

---

## Python packages

| Package | License | Copyright / project |
|---|---|---|
| FastAPI | MIT | © Sebastián Ramírez — https://github.com/fastapi/fastapi |
| Starlette (via FastAPI) | BSD-3-Clause | © Encode OSS Ltd — https://github.com/encode/starlette |
| Uvicorn | BSD-3-Clause | © Encode OSS Ltd — https://github.com/encode/uvicorn |
| python-multipart | Apache-2.0 | © Andrew Dunham — https://github.com/Kludex/python-multipart |
| **PyMuPDF** (`fitz`) | **AGPL-3.0** (or Artifex commercial) | © Artifex Software / MuPDF — https://github.com/pymupdf/PyMuPDF |
| pdf2docx | MIT | © Artifex Software and contributors — https://github.com/ArtifexSoftware/pdf2docx |
| openpyxl | MIT | © openpyxl authors — https://foss.heptapod.net/openpyxl/openpyxl |
| python-pptx | MIT | © Steve Canny — https://github.com/scanny/python-pptx |
| Markdown (Python-Markdown) | BSD-3-Clause | © Python Markdown Project — https://github.com/Python-Markdown/markdown |
| WeasyPrint | BSD-3-Clause | © Simon Sapin and contributors (CourtBouillon) — https://github.com/Kozea/WeasyPrint |
| Pygments | BSD-2-Clause | © Pygments team — https://github.com/pygments/pygments |
| pyHanko | MIT | © Matthias Valvekens — https://github.com/MatthiasValvekens/pyHanko |

Transitive dependencies (e.g. Pillow, pydyf, tinycss2, cssselect2, fonttools,
pyphen, cryptography, asn1crypto) are covered by their own licenses, which are
delivered with those packages by pip.

## System engines and libraries (installed by the Dockerfile)

| Component | License | Notes |
|---|---|---|
| **Ghostscript** | **AGPL-3.0** (or Artifex commercial) | compress, grayscale, PDF/A — https://www.ghostscript.com |
| qpdf | Apache-2.0 | repair — https://github.com/qpdf/qpdf |
| LibreOffice | MPL-2.0 | Office → PDF — https://www.libreoffice.org |
| Tesseract OCR | Apache-2.0 | OCR engine — https://github.com/tesseract-ocr/tesseract |
| OCRmyPDF | MPL-2.0 | OCR / PDF-A driver — https://github.com/ocrmypdf/OCRmyPDF |
| unpaper | GPL-2.0 | optional OCR deskew — https://github.com/unpaper/unpaper |
| pngquant | GPL-3.0 | optional OCR image optimisation — https://pngquant.org |
| Pango | LGPL-2.1-or-later | text layout for WeasyPrint (dynamically linked) — https://pango.gnome.org |
| util-linux (`prlimit`) | GPL-2.0 | subprocess resource limits — https://github.com/util-linux/util-linux |
| ExifTool (Image::ExifTool) | Artistic-1.0 / GPL-1.0-or-later | installed; currently not used by any tool — https://exiftool.org |
| DejaVu fonts | Bitstream Vera / Arev (permissive) | default fonts — https://dejavu-fonts.github.io |

The GPL/LGPL command-line tools above are invoked as **separate processes**
(fork-exec) or, for Pango, used as a **dynamically-linked system library**.
Neither form places their copyleft on Squish's own source under the terms of
those licenses; they remain independently licensed as listed.

## Frontend (vendored)

| Component | License | Notes |
|---|---|---|
| pdf.js | Apache-2.0 | © Mozilla — thumbnails / page picker; served from `/static/vendor/` — https://github.com/mozilla/pdf.js |

---

## License texts

### MIT License

Applies to: FastAPI, pdf2docx, openpyxl, python-pptx, pyHanko.

```
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### BSD 3-Clause License

Applies to: Starlette, Uvicorn, WeasyPrint, Python-Markdown.

```
Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its contributors
   may be used to endorse or promote products derived from this software
   without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

### BSD 2-Clause License

Applies to: Pygments. Same as the 3-Clause text above without the third
(non-endorsement) clause.

### Apache License 2.0

Applies to: python-multipart, qpdf, Tesseract OCR, pdf.js. Full text:
https://www.apache.org/licenses/LICENSE-2.0

### Mozilla Public License 2.0

Applies to: LibreOffice, OCRmyPDF. Full text: https://www.mozilla.org/MPL/2.0/

### GNU Lesser General Public License v2.1

Applies to: Pango (dynamically linked). Full text:
https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html

### GNU General Public License (v2.0 / v3.0)

Applies to: unpaper (GPL-2.0), pngquant (GPL-3.0), util-linux (GPL-2.0), invoked
as separate programs. Full texts: https://www.gnu.org/licenses/

### GNU Affero General Public License v3.0

Applies to: PyMuPDF/MuPDF, Ghostscript, and Squish itself. Full text is in the
repository `LICENSE` file, or https://www.gnu.org/licenses/agpl-3.0.html

---

*Corrections welcome. If a version bump changes any license above, update this
file in the same commit.*
