# Vendored Tesseract runtime

Squish includes these files so browser OCR never depends on a runtime CDN:

- Tesseract.js 6.0.1 (`tesseract.esm.min.js`, `worker.min.js`)
- tesseract.js-core 6.1.2 (WASM loader variants and binaries)
- `tessdata_best_int` language models for `eng`, `fra`, `deu`, `spa`, `por`
  and `ita`

The JavaScript packages are Apache-2.0 licensed. The trained-data files retain
their upstream licenses; see the adjacent license files. Versions are pinned
because Tesseract.js 7.0.0 referenced an unpublished matching core package when
this bundle was assembled.

Upstream sources:

- https://github.com/naptha/tesseract.js
- https://github.com/naptha/tesseract.js-core
- https://github.com/naptha/tessdata/tree/4.0.0_best_int
