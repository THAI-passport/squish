#!/usr/bin/env bash
# Run Squish locally: docker if available, else a native venv.
#
# The version check at the end is the point of this script. A stale uvicorn
# holding port 8000 will happily serve old code forever and look perfectly
# healthy; hard-failing unless /api/health reports the expected version turns
# hours of phantom debugging into one line of output.
set -euo pipefail
cd "$(dirname "$0")"

MARKER="-squish"          # substring APP_VERSION must contain
PORT="${PORT:-8000}"
MODE="${MODE:-auto}"      # auto | docker | native

echo "== 1/4 verify source =="
grep -q 'APP_VERSION' backend/app.py || { echo "FAIL: backend/app.py has no APP_VERSION"; exit 1; }
VERSION=$(grep -o "\"[^\"]*${MARKER}[^\"]*\"" backend/app.py | head -1 | tr -d '"\r')
[ -n "$VERSION" ] || { echo "FAIL: APP_VERSION must contain '${MARKER}'"; exit 1; }
echo "source version: $VERSION"

echo "== 2/4 stop anything on :$PORT =="
if pkill -f "uvicorn app:app" 2>/dev/null; then echo "killed stale uvicorn"; fi
if [ "$MODE" = auto ]; then
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then MODE=docker; else MODE=native; fi
fi
if [ "$MODE" = docker ]; then
  docker compose down --remove-orphans 2>/dev/null || true
else
  PIDS=$(lsof -ti ":$PORT" 2>/dev/null || true)
  if [ -z "$PIDS" ] && command -v netstat >/dev/null 2>&1; then
    PIDS=$(netstat -ano 2>/dev/null | grep ":$PORT " | grep -i "listening" | awk '{print $NF}' | sort -u || true)
  fi
  if [ -n "$PIDS" ]; then
    for pid in $PIDS; do
      kill "$pid" 2>/dev/null || taskkill //F //PID "$pid" 2>/dev/null || true
    done
    sleep 1
  fi
fi
echo "mode: $MODE"

echo "== 3/4 start =="
if [ "$MODE" = docker ]; then
  docker compose up -d --build
else
  PY=""
  if command -v python3 >/dev/null 2>&1; then PY="python3"
  elif command -v python >/dev/null 2>&1; then PY="python"
  elif command -v py >/dev/null 2>&1; then PY="py -3"
  else echo "FAIL: python3 or python missing"; exit 1; fi

  # Native mode uses whatever engines are on your PATH. Missing ones are not
  # fatal: /api/tools reports them and the UI greys those tools out.
  missing=""
  for bin in gs qpdf soffice ocrmypdf exiftool; do
    found=""
    if command -v "$bin" >/dev/null 2>&1; then
      found=1
    elif [ "$bin" = "gs" ] && command -v gswin64c >/dev/null 2>&1; then
      found=1
    elif [ "$bin" = "soffice" ] && { [ -f "/c/Program Files/LibreOffice/program/soffice.exe" ] || [ -f "C:/Program Files/LibreOffice/program/soffice.exe" ] || [ -f "/cygdrive/c/Program Files/LibreOffice/program/soffice.exe" ]; }; then
      found=1
    elif [ "$bin" = "gs" ] && { ls /c/Program\ Files/gs/gs*/bin/gswin64c.exe >/dev/null 2>&1 || ls /cygdrive/c/Program\ Files/gs/gs*/bin/gswin64c.exe >/dev/null 2>&1; }; then
      found=1
    fi
    if [ -z "$found" ]; then
      echo "note: $bin not found -- related tools disabled"
      missing=1
    fi
  done
  [ -n "$missing" ] && echo "  to enable them, see the Running on Windows section in README.md (or just use docker)"
  # Best-effort vendor of pdf.js for in-browser thumbnails. Non-fatal: without
  # it the UI just shows document icons instead of page previews.
  if [ ! -f backend/static/vendor/pdf.min.js ]; then
    ( cd backend/static/vendor && bash ./fetch-pdfjs.sh ) \
      || echo "note: pdf.js not vendored -- thumbnails will fall back to icons"
  fi
  [ -d .venv ] || $PY -m venv .venv
  if [ -d .venv/Scripts ]; then
    VENV_BIN=".venv/Scripts"
  else
    VENV_BIN=".venv/bin"
  fi
  if [ -f "$VENV_BIN/python.exe" ]; then
    VENV_PY="$VENV_BIN/python.exe"
  else
    VENV_PY="$VENV_BIN/python"
  fi

  "$VENV_PY" -m pip install -q --upgrade pip
  "$VENV_PY" -m pip install -q -r backend/requirements.txt
  ( cd backend && nohup "../$VENV_BIN/python" -m uvicorn app:app \
      --host 127.0.0.1 --port "$PORT" --timeout-keep-alive 120 \
      > ../squish.log 2>&1 & )
  echo "logs: $(pwd)/squish.log"
fi

echo "== 4/4 wait for health =="
ok=""
for _ in $(seq 1 60); do
  curl -sf "http://localhost:$PORT/api/health" >/dev/null 2>&1 && { ok=1; break; }
  sleep 1
done
if [ -z "$ok" ]; then
  echo "FAIL: no health response."
  if [ "$MODE" = native ]; then tail -30 squish.log; else docker compose logs --tail 40; fi
  exit 1
fi
curl -s "http://localhost:$PORT/api/health" | tr -d '\r'; echo
curl -s "http://localhost:$PORT/api/health" | tr -d '\r' | grep -q -e "$MARKER" || {
  echo "VERDICT: OLD SERVER still answering on :$PORT"; exit 1; }
echo "VERDICT: NEW CODE RUNNING ($VERSION)"
if command -v open >/dev/null 2>&1; then
  open "http://localhost:$PORT"
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "http://localhost:$PORT" >/dev/null 2>&1 &
elif command -v cmd.exe >/dev/null 2>&1; then
  cmd.exe /c start "http://localhost:$PORT" >/dev/null 2>&1 || true
elif command -v start >/dev/null 2>&1; then
  start "http://localhost:$PORT" >/dev/null 2>&1 || true
fi
echo "Open http://localhost:$PORT"
