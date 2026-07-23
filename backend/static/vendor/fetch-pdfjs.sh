#!/usr/bin/env bash
# Vendor pdf.js into this folder so PDF thumbnails render with NO external
# request at runtime. Run this once; the two files it downloads are what the
# UI loads from /static/vendor/. They are gitignored-by-size? No -- commit them,
# that is the whole point of vendoring (self-contained, offline-capable).
#
#   cd backend/static/vendor && ./fetch-pdfjs.sh
#
# Pinned to a UMD (non-module) legacy build so it works with the single-file,
# no-build UI: it exposes a global `pdfjsLib` and needs no bundler.
set -euo pipefail

VER="3.11.174"
BASE="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/${VER}"

fetch() {
  local url="$1" out="$2"
  if command -v curl >/dev/null; then
    curl -fsSL "$url" -o "$out"
  elif command -v wget >/dev/null; then
    wget -qO "$out" "$url"
  else
    echo "need curl or wget" >&2; exit 1
  fi
  echo "  $out  ($(wc -c < "$out") bytes)"
}

echo "vendoring pdf.js ${VER} ..."
fetch "${BASE}/pdf.min.js"        pdf.min.js
fetch "${BASE}/pdf.worker.min.js" pdf.worker.min.js
echo "done. Thumbnails will render on the next page load."
