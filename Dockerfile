# One image, two targets: docker compose on a laptop and Kubernetes in a
# cluster. Nothing in here is environment-specific.
#
# Build args let you drop the heavy engines when you don't need them:
#   docker build --build-arg WITH_OFFICE=0 --build-arg WITH_OCR=0 .
# That takes the image from ~1.5 GB to ~350 MB; /api/tools then reports those
# tools as unavailable and the UI greys them out.

FROM python:3.12-slim AS base

ARG WITH_OFFICE=1
ARG WITH_OCR=1
ARG OCR_LANGS="eng fra deu spa por ita"

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp

# Ghostscript and qpdf are small and power compress/repair -- always installed.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ghostscript qpdf fonts-dejavu-core \
      util-linux; \
    if [ "$WITH_OCR" = "1" ]; then \
      apt-get install -y --no-install-recommends ocrmypdf tesseract-ocr unpaper pngquant; \
      for l in $OCR_LANGS; do \
        [ "$l" = "eng" ] || apt-get install -y --no-install-recommends "tesseract-ocr-$l"; \
      done; \
    fi; \
    if [ "$WITH_OFFICE" = "1" ]; then \
      apt-get install -y --no-install-recommends \
        libreoffice-writer libreoffice-calc libreoffice-impress \
        libreoffice-core default-jre-headless; \
    fi; \
    rm -rf /var/lib/apt/lists/*

# Bake a warm LibreOffice profile. Building one on first use costs 3-5 seconds,
# and since concurrent conversions cannot share a profile, every request would
# otherwise pay it. Copying this template per request is a few MB of file I/O.
RUN set -eux; \
    if [ "$WITH_OFFICE" = "1" ]; then \
      mkdir -p /opt/lo-seed /opt/lo-profile; \
      printf 'warmup\n' > /opt/lo-seed/warm.txt; \
      HOME=/tmp soffice --headless --norestore --nolockcheck \
        -env:UserInstallation=file:///opt/lo-profile \
        --convert-to pdf --outdir /opt/lo-seed /opt/lo-seed/warm.txt || true; \
      rm -rf /opt/lo-seed; \
      chmod -R a+rX /opt/lo-profile; \
    fi

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app.py backend/tools.py ./
COPY backend/static ./static

# Non-root, and no writable home: everything transient goes to /tmp, which is
# an emptyDir on Kubernetes so readOnlyRootFilesystem still works.
RUN useradd -r -u 10001 -d /tmp squish && chown -R squish /app
USER squish

ENV SCRATCH_DIR=/tmp \
    MAX_UPLOAD_MB=200 \
    MAX_TOTAL_UPLOAD_MB=400 \
    MAX_OUTPUT_MB=1024 \
    MAX_PAGES=5000 \
    MAX_RENDER_MP=4000 \
    SUBPROC_MEM_MB=1536 \
    LO_PROFILE_TEMPLATE=/opt/lo-profile \
    MAX_CONCURRENCY=4

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", \
     "--timeout-keep-alive", "120"]
