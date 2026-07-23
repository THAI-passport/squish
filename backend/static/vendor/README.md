# Vendored third-party assets

The UI renders PDF thumbnails in the browser with **pdf.js** (Mozilla's PDF
engine). It is loaded from here, not a CDN, so the page makes **zero external
requests** — the same guarantee as the rest of Squish.

## Getting the files

Two files are required and are **not** committed to keep the diff clean:

- `pdf.min.js` (~350 KB)
- `pdf.worker.min.js` (~1 MB)

Fetch them once:

```bash
cd backend/static/vendor
./fetch-pdfjs.sh
```

Or download them manually from
`https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/` and drop them in this
folder.

The Docker image runs the fetch at build time, so containers ship with
thumbnails working out of the box.

## Graceful degradation

If these files are absent, the app still runs — file rows just show a document
icon instead of a rendered preview. Nothing errors.

## Licence

pdf.js is Apache-2.0, © Mozilla. Pinned to 3.11.174 (a UMD/legacy build that
exposes a global `pdfjsLib` and needs no bundler).
