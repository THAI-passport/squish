"""Tests for SMTP Manager and Secure Email Dispatch."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import fitz
import smtp_manager
import tools as T


def test_test_smtp_connection_validation():
    # Empty host fails validation
    res = smtp_manager.test_smtp_connection({})
    assert res["ok"] is False
    assert "required" in res["error"]


@patch("smtplib.SMTP")
def test_test_smtp_connection_success(mock_smtp):
    mock_inst = MagicMock()
    mock_smtp.return_value = mock_inst
    mock_inst.ehlo.return_value = (250, b"ok")

    res = smtp_manager.test_smtp_connection({
        "server": "smtp.example.com",
        "port": 587,
        "username": "user@example.com",
        "password": "secretpassword",
        "security": "starttls"
    })
    assert res["ok"] is True
    assert "Successfully connected" in res["message"]
    mock_inst.starttls.assert_called_once()
    mock_inst.login.assert_called_once_with("user@example.com", "secretpassword")


@patch("smtplib.SMTP")
def test_send_dual_secure_email(mock_smtp, tmp_path):
    mock_inst = MagicMock()
    mock_smtp.return_value = mock_inst
    mock_inst.ehlo.return_value = (250, b"ok")

    # Create dummy PDF
    pdf_path = tmp_path / "doc.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(pdf_path)
    doc.close()

    res = smtp_manager.send_dual_secure_email(
        smtp={
            "server": "smtp.example.com",
            "port": 587,
            "username": "sender@example.com",
            "password": "secretpassword",
            "from_name": "Finance Team"
        },
        recipient="client@example.com",
        pdf_path=pdf_path,
        password="MySecretKey123!",
        subject="Invoice #1024",
        html_body="<p>Please find attached.</p>",
        delay_seconds=0.1  # fast in tests
    )

    assert res["ok"] is True
    assert res["recipient"] == "client@example.com"
    # Verify sendmail was called twice (Email 1 with attachment, Email 2 with password)
    assert mock_inst.sendmail.call_count == 2


@patch("smtplib.SMTP")
def test_email_secure_tool(mock_smtp, tmp_path):
    mock_inst = MagicMock()
    mock_smtp.return_value = mock_inst

    work = tmp_path / "work"
    work.mkdir()

    # Create sample PDF
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
            "email_subject": "Confidential Report",
            "password_mode": "manual",
            "custom_password": "MySecretPass999!",
            "mail_server": "smtp.example.com",
            "mail_port": "587",
            "mail_username": "sender@example.com",
            "mail_password": "pwd",
            "delay_seconds": "0.1"
        }
    )

    assert result.media_type == "application/json"
    assert result.path.exists()
    data = json.loads(result.path.read_text(encoding="utf-8"))
    assert data["status"] == "success"
    assert data["recipient"] == "target@example.com"
    assert data["password"] == "MySecretPass999!"

    # Verify protected PDF was created with encryption
    protected_pdf = work / "sample_protected.pdf"
    assert protected_pdf.exists()

    # Open with fitz and test password
    locked_doc = fitz.open(protected_pdf)
    assert locked_doc.is_encrypted
    assert locked_doc.authenticate("MySecretPass999!") > 0
    locked_doc.close()


@patch("smtplib.SMTP")
def test_send_dual_secure_email_custom_templates(mock_smtp, tmp_path):
    mock_inst = MagicMock()
    mock_smtp.return_value = mock_inst
    mock_inst.ehlo.return_value = (250, b"ok")

    pdf_path = tmp_path / "quarterly_results.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(pdf_path)
    doc.close()

    res = smtp_manager.send_dual_secure_email(
        smtp={
            "server": "smtp.example.com",
            "port": 587,
            "username": "ceo@example.com",
            "password": "secretpassword",
            "from_name": "Executive Office"
        },
        recipient="board@example.com",
        pdf_path=pdf_path,
        password="TopSecretPassword456!",
        subject="Q3 Financials",
        html_body="<div class='invoice'><h3>Q3 Report</h3><p>Attached is the confidential financials PDF.</p></div>",
        email2_subject="Decryption Key for {{doc_name}}",
        email2_body="Hello Board,\n\nYour key is {{password}} for {{doc_name}}.\n\nBest,\nExecutive Office",
        delay_seconds=0.05
    )

    assert res["ok"] is True
    assert mock_inst.sendmail.call_count == 2

