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
        delay_seconds=0.1
    )

    assert res["ok"] is True
    assert res["recipient"] == "client@example.com"
    assert mock_inst.sendmail.call_count == 2


@patch("smtplib.SMTP")
def test_email_secure_tool(mock_smtp, tmp_path):
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

    protected_pdf = work / "sample_protected.pdf"
    assert protected_pdf.exists()

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


# ---------------------------------------------------------------------------
# Audit-fix regression tests: header sanitization, owner/user password
# separation, and partial-failure (Email #1 ok, Email #2 fails) handling.
# ---------------------------------------------------------------------------

def test_sanitize_header_value_strips_crlf():
    cleaned = smtp_manager.sanitize_header_value("Subject line\r\nX-Injected: yes\r\n")
    assert "\r" not in cleaned and "\n" not in cleaned
    assert cleaned == "Subject lineX-Injected: yes"


def test_validate_recipient_email_rejects_injection_and_multiple_addresses():
    assert smtp_manager.validate_recipient_email("client@example.com") == "client@example.com"
    with pytest.raises(ValueError):
        smtp_manager.validate_recipient_email("client@example.com\r\nBcc: attacker@evil.com")
    with pytest.raises(ValueError):
        smtp_manager.validate_recipient_email("a@example.com, b@example.com")
    with pytest.raises(ValueError):
        smtp_manager.validate_recipient_email("not an email")


@patch("smtplib.SMTP")
def test_send_dual_secure_email_sanitizes_subject_header(mock_smtp, tmp_path):
    mock_inst = MagicMock()
    mock_smtp.return_value = mock_inst
    mock_inst.ehlo.return_value = (250, b"ok")

    pdf_path = tmp_path / "doc.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(pdf_path)
    doc.close()

    smtp_manager.send_dual_secure_email(
        smtp={"server": "smtp.example.com", "port": 587, "username": "sender@example.com", "password": "pw"},
        recipient="client@example.com",
        pdf_path=pdf_path,
        password="Secret123!",
        subject="Invoice\r\nBcc: attacker@evil.com",
        delay_seconds=0.05,
    )

    first_call_msg = mock_inst.sendmail.call_args_list[0].args[2]
    header_lines = first_call_msg.split("\n\n", 1)[0].splitlines()
    # A CRLF-injected "Bcc:" must never appear as its OWN header line -- the
    # sanitizer should fold it into the Subject line's text instead.
    assert not any(line.strip().lower().startswith("bcc:") for line in header_lines)


def test_email_secure_owner_password_differs_from_user_password(tmp_path):
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

        T.email_secure(
            work=work,
            inputs=[src_pdf],
            p={
                "recipient_email": "target@example.com",
                "password_mode": "manual",
                "custom_password": "UserPass123!",
                "mail_server": "smtp.example.com",
                "mail_port": "587",
                "mail_username": "sender@example.com",
                "mail_password": "pwd",
                "delay_seconds": "0.05",
            }
        )

        protected_pdf = work / "sample_protected.pdf"
        locked_doc = fitz.open(protected_pdf)
        # authenticate() returns 2 for a user-level unlock (restricted
        # permissions) and 4 for owner-level (full permissions) -- these
        # must differ, otherwise the emailed user password also grants full
        # owner rights and the permission restriction is meaningless.
        auth_code = locked_doc.authenticate("UserPass123!")
        assert auth_code == 2, f"expected user-level auth (2), got {auth_code}"
        # Print is allowed, but the doc must NOT report full/owner permissions.
        assert locked_doc.permissions != -1
        locked_doc.close()

        # And a random string should NOT unlock it at all -- proves the
        # generated owner password isn't something predictable like the PDF
        # filename, empty string, or the user password itself.
        locked_doc2 = fitz.open(protected_pdf)
        assert locked_doc2.authenticate("UserPass123!-not-the-owner-pw") == 0
        locked_doc2.close()


@patch("smtplib.SMTP")
def test_send_dual_secure_email_partial_failure_on_email2(mock_smtp, tmp_path):
    mock_inst = MagicMock()
    mock_smtp.return_value = mock_inst
    mock_inst.ehlo.return_value = (250, b"ok")
    # First sendmail (Email #1) succeeds, second (Email #2) raises.
    mock_inst.sendmail.side_effect = [None, Exception("connection reset")]

    pdf_path = tmp_path / "doc.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(pdf_path)
    doc.close()

    res = smtp_manager.send_dual_secure_email(
        smtp={"server": "smtp.example.com", "port": 587, "username": "sender@example.com", "password": "pw"},
        recipient="client@example.com",
        pdf_path=pdf_path,
        password="Secret123!",
        delay_seconds=0.05,
    )

    assert res["ok"] is False
    assert res["partial"] is True
    assert res["step1_sent"] is True
    assert res["step2_sent"] is False
    assert "connection reset" in res["error"]


@patch("smtplib.SMTP")
def test_send_dual_secure_email_total_failure_when_email1_fails(mock_smtp, tmp_path):
    mock_inst = MagicMock()
    mock_smtp.return_value = mock_inst
    mock_inst.ehlo.return_value = (250, b"ok")
    mock_inst.sendmail.side_effect = Exception("auth failed")

    pdf_path = tmp_path / "doc.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(pdf_path)
    doc.close()

    with pytest.raises(Exception, match="auth failed"):
        smtp_manager.send_dual_secure_email(
            smtp={"server": "smtp.example.com", "port": 587, "username": "sender@example.com", "password": "pw"},
            recipient="client@example.com",
            pdf_path=pdf_path,
            password="Secret123!",
            delay_seconds=0.05,
        )


@patch("smtplib.SMTP")
def test_email_secure_tool_reports_partial_failure(mock_smtp, tmp_path):
    mock_inst = MagicMock()
    mock_smtp.return_value = mock_inst
    mock_inst.sendmail.side_effect = [None, Exception("smtp hiccup")]

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
            "custom_password": "MySecretPass999!",
            "mail_server": "smtp.example.com",
            "mail_port": "587",
            "mail_username": "sender@example.com",
            "mail_password": "pwd",
            "delay_seconds": "0.05",
        }
    )

    data = json.loads(result.path.read_text(encoding="utf-8"))
    assert data["status"] == "partial_failure"
    assert data["step1_sent"] is True
    assert data["step2_sent"] is False
    assert data["password"] == "MySecretPass999!"
    assert "smtp" not in data["resend"]  # never round-trips the SMTP secret


@patch("smtplib.SMTP")
def test_send_key_notification_resends_password_only(mock_smtp):
    mock_inst = MagicMock()
    mock_smtp.return_value = mock_inst
    mock_inst.ehlo.return_value = (250, b"ok")

    res = smtp_manager.send_key_notification(
        smtp={"server": "smtp.example.com", "port": 587, "username": "sender@example.com", "password": "pw"},
        recipient="client@example.com",
        password="Secret123!",
        pdf_filename="doc_protected.pdf",
    )
    assert res["ok"] is True
    assert mock_inst.sendmail.call_count == 1


@patch("smtplib.SMTP")
def test_send_dual_secure_email_threading(mock_smtp, tmp_path):
    mock_inst = MagicMock()
    mock_smtp.return_value = mock_inst
    mock_inst.ehlo.return_value = (250, b"ok")

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
        thread_emails=True,
        delay_seconds=0.05
    )

    assert res["ok"] is True
    assert mock_inst.sendmail.call_count == 2
    
    # Check the second email's raw MIME headers for In-Reply-To and References
    call_args_list = mock_inst.sendmail.call_args_list
    email1_raw = call_args_list[0][0][2]
    email2_raw = call_args_list[1][0][2]

    # Email 1 must have Message-ID
    assert "Message-ID:" in email1_raw
    # Email 2 must have In-Reply-To and References pointing to Email 1
    assert "In-Reply-To:" in email2_raw
    assert "References:" in email2_raw
    assert "Subject: Re: [Secure Document] Invoice #1024" in email2_raw
