"""HTTP layer: routing, guards, headers, and the upload path."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as A


@pytest.fixture
def client():
    return TestClient(A.app)


# ---------------------------------------------------------------- health ---

def test_health_reports_a_version(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and "-squish" in body["version"]


def test_health_lists_engine_availability(client):
    engines = client.get("/api/health").json()["engines"]
    assert set(engines) == {"gs", "soffice", "tesseract", "ocrmypdf", "qpdf", "exiftool"}
    assert all(isinstance(v, bool) for v in engines.values())


def test_health_publishes_the_limits(client):
    body = client.get("/api/health").json()
    for key in ("max_upload_mb", "max_output_mb", "max_pages", "max_render_mp"):
        assert key in body


# ----------------------------------------------------------------- tools ---

def test_tools_endpoint_matches_the_registry(client):
    import tools as T
    listed = client.get("/api/tools").json()["tools"]
    assert {t["key"] for t in listed} == set(T.REGISTRY)


def test_tools_mark_missing_engines_unavailable(client, monkeypatch):
    monkeypatch.setattr(A.shutil, "which", lambda n: None)
    listed = client.get("/api/tools").json()["tools"]
    by_key = {t["key"]: t for t in listed}
    assert by_key["compress"]["available"] is False
    assert by_key["compress"]["needs"] == "gs"
    # A tool with no external engine stays available regardless.
    assert by_key["merge"]["available"] is True


# --------------------------------------------------------------- routing ---

def test_unknown_tool_is_404(client):
    r = client.post("/api/t/does-not-exist", files={"files": ("a.pdf", b"%PDF-")})
    assert r.status_code == 404


def test_missing_files_is_400(client):
    assert client.post("/api/t/compress").status_code == 400


def test_multi_file_minimum_is_enforced(client, pdf):
    r = client.post("/api/t/merge",
                    files={"files": ("a.pdf", pdf.read_bytes(), "application/pdf")})
    assert r.status_code == 400
    assert "at least 2" in r.json()["detail"]


def test_too_many_files_is_rejected(client, pdf, monkeypatch):
    monkeypatch.setattr(A, "MAX_FILES", 2)
    data = pdf.read_bytes()
    files = [("files", (f"f{i}.pdf", data, "application/pdf")) for i in range(3)]
    r = client.post("/api/t/merge", files=files)
    assert r.status_code == 400 and "too many files" in r.json()["detail"]


def test_empty_upload_is_rejected(client):
    r = client.post("/api/t/split",
                    files={"files": ("empty.pdf", b"", "application/pdf")})
    assert r.status_code == 400


def test_magic_bytes_rejected(client):
    r = client.post("/api/t/split",
                    files={"files": ("bad.pdf", b"this is not a pdf file", "application/pdf")})
    assert r.status_code == 400
    assert "valid PDF" in r.json()["detail"]


def test_oversized_upload_is_rejected_midstream(client, monkeypatch):
    """The cap must bite while receiving, not after buffering the whole file."""
    monkeypatch.setattr(A, "MAX_UPLOAD_MB", 1)
    blob = io.BytesIO(b"%PDF-1.4\n" + b"\0" * (3 * 1024 * 1024))
    r = client.post("/api/t/split",
                    files={"files": ("big.pdf", blob, "application/pdf")})
    assert r.status_code == 413


# --------------------------------------------------------------- success ---

def test_split_returns_a_pdf_with_a_filename(client, pdf):
    r = client.post("/api/t/split", data={"pages": "1"},
                    files={"files": ("sample.pdf", pdf.read_bytes(), "application/pdf")})
    if r.status_code != 200:
        print(r.text)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert "attachment" in r.headers["content-disposition"]
    assert r.content.startswith(b"%PDF")


def test_tool_errors_become_400_not_500(client, pdf):
    r = client.post("/api/t/split", data={"pages": "99-200"},
                    files={"files": ("sample.pdf", pdf.read_bytes(), "application/pdf")})
    assert r.status_code == 400
    assert "outside document" in r.json()["detail"]


def test_content_disposition_survives_a_unicode_name(client, pdf):
    """latin-1 is the only legal encoding for that header; an emoji in a
    filename must not become a 500 on the way out."""
    r = client.post("/api/t/split", data={"pages": "1"},
                    files={"files": ("réport-📄.pdf", pdf.read_bytes(), "application/pdf")})
    assert r.status_code == 200
    r.headers["content-disposition"].encode("latin-1")   # raises if invalid


def test_output_size_ceiling(client, pdf, monkeypatch):
    monkeypatch.setattr(A, "MAX_OUTPUT_MB", 0)
    r = client.post("/api/t/split", data={"pages": "1"},
                    files={"files": ("sample.pdf", pdf.read_bytes(), "application/pdf")})
    assert r.status_code == 507


# ----------------------------------------------------------------- guard ---

def test_api_key_is_enforced_when_set(client, monkeypatch):
    monkeypatch.setattr(A, "API_KEY", "sekrit")
    assert client.post("/api/t/split").status_code == 401
    # Health stays open so probes keep working without the key.
    assert client.get("/api/health").status_code == 200


def test_api_key_accepts_the_header(client, pdf, monkeypatch):
    monkeypatch.setattr(A, "API_KEY", "sekrit")
    r = client.post("/api/t/split", data={"pages": "1"},
                    headers={"X-API-Key": "sekrit"},
                    files={"files": ("s.pdf", pdf.read_bytes(), "application/pdf")})
    assert r.status_code == 200


def test_api_key_is_not_accepted_in_query_string(client, monkeypatch):
    monkeypatch.setattr(A, "API_KEY", "sekrit")
    assert client.get("/api/tools?key=sekrit").status_code == 401


def test_cross_origin_state_change_is_rejected(client):
    r = client.post(
        "/api/t/split",
        headers={"Origin": "https://attacker.example", "Sec-Fetch-Site": "cross-site"},
    )
    assert r.status_code == 403
    assert "cross-origin" in r.json()["detail"]


def test_same_origin_state_change_reaches_handler(client):
    r = client.post(
        "/api/t/split",
        headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "no files uploaded"


def test_sensitive_smtp_routes_require_configured_api_key(client, monkeypatch):
    monkeypatch.setattr(A, "API_KEY", None)
    assert client.post("/api/smtp/test", json={}).status_code == 403
    assert client.post("/api/smtp/resend-key", json={}).status_code == 403
    assert client.post("/api/t/email-secure").status_code == 403


def test_smtp_test_returns_structured_safe_diagnostic(client, monkeypatch):
    import smtp_manager

    monkeypatch.setattr(A, "API_KEY", "test-api-key")
    monkeypatch.setattr(smtp_manager, "test_smtp_connection", lambda _config: {
        "ok": False,
        "stage": "authentication",
        "category": "credentials_rejected",
        "smtp_code": 535,
        "error": "The SMTP server rejected the username or password.",
        "hint": "Confirm the authentication username and account policy.",
        "relay_response": "5.7.8 Authentication rejected",
        "diagnostic_id": "a1b2c3d4",
    })
    response = client.post(
        "/api/smtp/test",
        headers={"X-API-Key": "test-api-key"},
        json={"smtp": {
            "server": "smtp.example.com", "port": 587,
            "username": "sender@example.com", "password": "must-not-leak",
            "security": "starttls",
        }},
    )
    body = response.json()
    assert response.status_code == 400
    assert body["stage"] == "authentication"
    assert body["smtp_code"] == 535
    assert body["diagnostic_id"] == "a1b2c3d4"
    assert "must-not-leak" not in response.text


# --------------------------------------------------------------- metrics ---

def test_metrics_counts_jobs(client, pdf):
    client.post("/api/t/split", data={"pages": "1"},
                files={"files": ("s.pdf", pdf.read_bytes(), "application/pdf")})
    body = client.get("/metrics").text
    assert 'squish_jobs_total{tool="split",outcome="ok"}' in body
    assert "squish_inflight" in body


def test_metrics_records_failures_separately(client, pdf):
    client.post("/api/t/split", data={"pages": "500"},
                files={"files": ("s.pdf", pdf.read_bytes(), "application/pdf")})
    assert 'outcome="rejected"' in client.get("/metrics").text


def test_metrics_uses_api_key_when_configured(client, monkeypatch):
    monkeypatch.setattr(A, "API_KEY", "sekrit")
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"X-API-Key": "sekrit"}).status_code == 200


# ------------------------------------------------------------ filenames ---

@pytest.mark.parametrize("given", ["../../../etc/passwd", "a/b/c.pdf", "..\\x.pdf"])
def test_upload_filenames_cannot_escape(given):
    out = A.safe_filename(given)
    assert "/" not in out and "\\" not in out and not out.startswith(".")


def test_static_ui_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Squish" in r.text
    assert r.headers["cache-control"] == "no-cache"


# ------------------------------------------------- scratch dir lifecycle ---
# Regression guard for the leak: BackgroundTask.__call__ is a coroutine, so
# calling it synchronously on an error path never deleted anything, and every
# rejected job left its temp dir behind for the life of the process.

def _scratch_dirs():
    return list(Path(A.SCRATCH).glob("squish-*"))


def test_rejected_job_leaves_no_scratch_dir(client, pdf):
    before = _scratch_dirs()
    r = client.post("/api/t/split", data={"pages": "9999"},
                    files={"files": ("s.pdf", pdf.read_bytes(), "application/pdf")})
    assert r.status_code == 400
    assert _scratch_dirs() == before


def test_crashed_job_leaves_no_scratch_dir(client, pdf, monkeypatch):
    def boom(work, inputs, p):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(A.T.REGISTRY["split"], "fn", boom)
    before = _scratch_dirs()
    r = client.post("/api/t/split",
                    files={"files": ("s.pdf", pdf.read_bytes(), "application/pdf")})
    assert r.status_code == 500
    assert _scratch_dirs() == before


def test_timeout_keeps_the_concurrency_slot_until_the_thread_ends(client, pdf,
                                                                 monkeypatch):
    """A timeout must not hand the slot back while the worker still runs.

    asyncio.to_thread cannot be cancelled, so releasing the semaphore on
    timeout let unlimited orphan threads accumulate behind a limit that read as
    healthy.
    """
    import threading
    release = threading.Event()

    def slow(work, inputs, p):
        release.wait(30)
        raise RuntimeError("orphan finished")

    monkeypatch.setattr(A.T.REGISTRY["split"], "fn", slow)
    monkeypatch.setattr(A, "JOB_TIMEOUT", 1)
    with client:
        r = client.post("/api/t/split",
                        files={"files": ("s.pdf", pdf.read_bytes(), "application/pdf")})
        assert r.status_code == 504
        # Still held: the orphan thread has not finished yet.
        assert A.sem._value < A.MAX_CONCURRENCY
    release.set()


def test_body_over_the_total_cap_is_refused_before_parsing(client, monkeypatch):
    monkeypatch.setattr(A, "MAX_TOTAL_UPLOAD_MB", 1)
    blob = io.BytesIO(b"%PDF-1.4\n" + b"\0" * (2 * 1024 * 1024))
    r = client.post("/api/t/split",
                    files={"files": ("big.pdf", blob, "application/pdf")})
    assert r.status_code == 413
    assert "total" in r.json()["detail"] or "body" in r.json()["detail"]


def test_health_publishes_the_total_upload_cap(client):
    assert "max_total_upload_mb" in client.get("/api/health").json()


# ------------------------------------------------ magic-byte validation ---

def test_non_pdf_is_rejected_on_a_pdf_tool(client):
    r = client.post("/api/t/split", data={"pages": "1"},
                    files={"files": ("evil.pdf", b"this is not a pdf at all", "application/pdf")})
    assert r.status_code == 400
    assert "valid PDF" in r.json()["detail"]


def test_pdf_with_leading_bom_passes_the_magic_gate(client):
    # A BOM before %PDF- must not be rejected by the magic check. It will still
    # fail to parse (not a real PDF), but with a DIFFERENT error than the gate.
    body = b"\xef\xbb\xbf%PDF-1.4 not really a document"
    r = client.post("/api/t/split", data={"pages": "1"},
                    files={"files": ("x.pdf", body, "application/pdf")})
    assert "valid PDF" not in r.json().get("detail", "")


def test_repair_is_exempt_from_the_magic_gate(client):
    # Repair exists to fix broken PDFs, which may lack a clean header. The magic
    # check must be skipped for it -- so the error must NOT be the gate message.
    r = client.post("/api/t/repair",
                    files={"files": ("broken.pdf", b"garbage without a header", "application/pdf")})
    assert "does not appear to be a valid PDF" not in r.json().get("detail", "")


def test_text_only_tool_accepts_no_file(client):
    # md-to-pdf has min_files=0; a request with only pasted text must pass the
    # upload guard (it may still 400 later if weasyprint is absent, but NOT with
    # "no files uploaded").
    r = client.post("/api/t/md-to-pdf", data={"md_text": "# Hi\n\ntext"})
    if r.headers.get("content-type", "").startswith("application/json"):
        assert "no files uploaded" not in r.json().get("detail", "")
    else:
        # WeasyPrint is present and rendered; the upload guard passed.
        assert r.content[:5] == b"%PDF-"
