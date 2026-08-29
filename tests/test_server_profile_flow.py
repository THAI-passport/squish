"""Tests for the server-side .env profile flow: email_secure resolving a
profile by id, and /api/smtp/test + /api/smtp/resend-key doing the same."""

import json
from unittest.mock import MagicMock, patch

import fitz
import pytest
import env_manager
import tools as T


def test_email_secure_resolves_server_profile_by_id(tmp_path, monkeypatch):
    monkeypatch.setattr(env_manager, "ENV_PATH", tmp_path / ".env")
    idx = env_manager.add_profile({
        "name": "CI Relay", "server": "smtp.example.com", "port": 587,
        "username": "sender@example.com", "password": "server-side-secret",
    })

    with patch("smtplib.SMTP") as mock_smtp:
        mock_inst = MagicMock()
        mock_smtp.return_value = mock_inst

        work = tmp_path / "work"
        work.mkdir()
        src_pdf = tmp_path / "sample.pdf"
        doc = fitz.open()
        doc.new_page()
        doc.save(src_pdf)
        doc.close()

        result = T.email_secure(
            work=work,
            inputs=[src_pdf],
            p={
                "recipient_email": "target@example.com",
                "password_mode": "manual",
                "custom_password": "Pass123!",
                "smtp_server_profile_id": str(idx),
                "delay_seconds": "0.05",
            }
        )
        # The server (not the browser) supplied the password used to auth.
        mock_inst.login.assert_called_once_with("sender@example.com", "server-side-secret")
        data = json.loads(result.path.read_text(encoding="utf-8"))
        assert data["status"] == "success"


def test_email_secure_missing_server_profile_id_raises_clean_error(tmp_path, monkeypatch):
    monkeypatch.setattr(env_manager, "ENV_PATH", tmp_path / ".env")
    work = tmp_path / "work"
    work.mkdir()
    src_pdf = tmp_path / "sample.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(src_pdf)
    doc.close()

    with pytest.raises(T.ToolError, match="SMTP profile not found"):
        T.email_secure(
            work=work,
            inputs=[src_pdf],
            p={
                "recipient_email": "target@example.com",
                "password_mode": "manual",
                "custom_password": "Pass123!",
                "smtp_server_profile_id": "99",
            }
        )
