"""Tests for SMTP Manager and Secure Email Dispatch."""

import json
import smtplib
import socket
import ssl
from datetime import datetime, timedelta, timezone
from email import message_from_string
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import fitz
import smtp_manager
import tools as T


def _make_test_pdf(path, labels=("Page one",)):
    doc = fitz.open()
    for label in labels:
        page = doc.new_page()
        page.insert_text((72, 72), label)
    doc.save(path)
    doc.close()


def _make_test_pkcs12(path, password):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Squish Test Signer")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    path.write_bytes(pkcs12.serialize_key_and_certificates(
        b"squish-test", key, cert, None,
        serialization.BestAvailableEncryption(password.encode("utf-8")),
    ))


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
def test_smtp_diagnostic_identifies_rejected_credentials_without_leaking_secret(mock_smtp):
    mock_inst = MagicMock()
    mock_smtp.return_value = mock_inst
    mock_inst.login.side_effect = smtplib.SMTPAuthenticationError(
        535, b"5.7.8 Authentication credentials invalid\r\nretry denied"
    )

    res = smtp_manager.test_smtp_connection({
        "server": "smtp.example.com", "port": 587,
        "username": "user@example.com", "password": "do-not-leak-this",
        "security": "starttls",
    })

    assert res["ok"] is False
    assert res["stage"] == "authentication"
    assert res["category"] == "credentials_rejected"
    assert res["smtp_code"] == 535
    assert res["relay_response"] == "5.7.8 Authentication credentials invalidretry denied"
    assert len(res["diagnostic_id"]) == 8
    assert "do-not-leak-this" not in json.dumps(res)


@patch("smtplib.SMTP")
def test_smtp_diagnostic_identifies_auth_not_supported(mock_smtp):
    mock_inst = MagicMock()
    mock_smtp.return_value = mock_inst
    mock_inst.login.side_effect = smtplib.SMTPNotSupportedError(
        "SMTP AUTH extension not supported by server"
    )

    res = smtp_manager.test_smtp_connection({
        "server": "relay.example.com", "port": 587,
        "username": "user@example.com", "password": "pw",
        "security": "starttls",
    })

    assert res["stage"] == "authentication"
    assert res["category"] == "auth_not_supported"
    assert "IP allowlisting" in res["hint"]


@patch("smtplib.SMTP", side_effect=socket.gaierror(-2, "Name or service not known"))
def test_smtp_diagnostic_identifies_dns_failure(_mock_smtp):
    res = smtp_manager.test_smtp_connection({
        "server": "missing.invalid", "port": 587,
        "username": "user@example.com", "password": "pw",
        "security": "starttls",
    })
    assert res["stage"] == "dns"
    assert res["category"] == "host_not_found"


@patch("smtplib.SMTP", side_effect=socket.timeout("timed out"))
def test_smtp_diagnostic_identifies_connection_timeout(_mock_smtp):
    res = smtp_manager.test_smtp_connection({
        "server": "slow.example.com", "port": 587,
        "username": "user@example.com", "password": "pw",
        "security": "starttls",
    })
    assert res["stage"] == "connection"
    assert res["category"] == "timeout"


@patch("smtplib.SMTP_SSL", side_effect=ssl.SSLCertVerificationError("hostname mismatch"))
def test_smtp_diagnostic_identifies_certificate_failure(_mock_smtp):
    res = smtp_manager.test_smtp_connection({
        "server": "smtp.example.com", "port": 465,
        "username": "user@example.com", "password": "pw",
        "security": "ssl",
    })
    assert res["stage"] == "tls"
    assert res["category"] == "certificate_rejected"


def test_smtp_diagnostic_validation_has_stable_shape():
    res = smtp_manager.test_smtp_connection({})
    assert res["stage"] == "configuration"
    assert res["category"] == "invalid_configuration"
    assert res["diagnostic_id"]


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
def test_send_multiple_attachments_with_oob_key_only(mock_smtp, tmp_path):
    mock_inst = MagicMock()
    mock_smtp.return_value = mock_inst
    paths = []
    for name in ("contract.pdf", "appendix.pdf", "exhibit.pdf"):
        path = tmp_path / name
        _make_test_pdf(path)
        paths.append(path)

    result = smtp_manager.send_dual_secure_email(
        smtp={"server": "smtp.example.com", "port": 587,
              "username": "sender@example.com", "password": "pw"},
        recipient="client@example.com",
        pdf_path=paths,
        password="Secret123!",
        key_delivery_mode="oob",
    )

    assert result["attachments"] == [p.name for p in paths]
    assert result["step2_sent"] is False
    assert result["oob_required"] is True
    assert mock_inst.sendmail.call_count == 1
    parsed = message_from_string(mock_inst.sendmail.call_args.args[2])
    attached = [part.get_filename() for part in parsed.walk() if part.get_filename()]
    assert attached == [p.name for p in paths]


@patch("smtplib.SMTP")
def test_plain_text_template_omits_html_mime_part(mock_smtp, tmp_path):
    mock_inst = MagicMock()
    mock_smtp.return_value = mock_inst
    pdf = tmp_path / "notice.pdf"
    _make_test_pdf(pdf)
    smtp_manager.send_dual_secure_email(
        smtp={"server": "smtp.example.com", "port": 587,
              "username": "sender@example.com", "password": "pw"},
        recipient="client@example.com", pdf_path=pdf, password="Secret123!",
        html_body="Hello client,\n\nYour protected document is attached.",
        plain_text_only=True, key_delivery_mode="oob",
    )
    parsed = message_from_string(mock_inst.sendmail.call_args.args[2])
    content_types = [part.get_content_type() for part in parsed.walk()]
    assert "text/plain" in content_types
    assert "text/html" not in content_types


def test_attachment_bundle_limits_are_enforced_before_smtp(tmp_path):
    paths = []
    for index in range(11):
        path = tmp_path / f"doc-{index}.pdf"
        path.write_bytes(b"%PDF-1.7\n")
        paths.append(path)
    with pytest.raises(ValueError, match="maximum of 10"):
        smtp_manager.send_dual_secure_email(
            smtp={"server": "smtp.example.com"}, recipient="client@example.com",
            pdf_path=paths, password="Secret123!", key_delivery_mode="oob")

    oversized = tmp_path / "oversized.pdf"
    with oversized.open("wb") as stream:
        stream.truncate(smtp_manager.MAX_TOTAL_ATTACHMENT_BYTES + 1)
    with pytest.raises(ValueError, match="12 MB combined"):
        smtp_manager.send_dual_secure_email(
            smtp={"server": "smtp.example.com"}, recipient="client@example.com",
            pdf_path=oversized, password="Secret123!", key_delivery_mode="oob")


def test_split_dispatch_creates_isolated_page_tree(tmp_path):
    source = tmp_path / "master.pdf"
    _make_test_pdf(source, ("ALPHA ONLY", "BRAVO ONLY"))
    doc = fitz.open(source)
    doc.set_metadata({"title": "Master secret metadata", "author": "Hidden author"})
    doc.set_toc([[1, "Alpha", 1], [1, "Bravo", 2]])
    doc.save(tmp_path / "master_with_catalog.pdf")
    doc.close()

    work = tmp_path / "work"
    work.mkdir()
    isolated = T._dispatch_extract_pages(work, tmp_path / "master_with_catalog.pdf", "2")
    output = fitz.open(isolated)
    assert output.page_count == 1
    assert "BRAVO ONLY" in output[0].get_text()
    assert "ALPHA ONLY" not in output[0].get_text()
    assert output.get_toc() == []
    assert not output.metadata.get("title")
    assert not output.metadata.get("author")
    output.close()


def test_sign_dispatch_encrypts_then_adds_valid_incremental_signature(tmp_path):
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign import validation
    from pyhanko_certvalidator import ValidationContext

    source = tmp_path / "contract.pdf"
    _make_test_pdf(source, ("Signed contract",))
    cert = tmp_path / "signer.p12"
    _make_test_pkcs12(cert, "certificate-secret")
    work = tmp_path / "work"
    work.mkdir()

    encrypted = T._dispatch_protect_pdf(
        work, source, "RecipientSecret!", "IndependentOwnerSecret!", "encrypted",
        sign_compatible=True)
    signed = T._dispatch_sign_encrypted_pdf(
        work, encrypted, "RecipientSecret!", cert, "certificate-secret", "SquishSignature1")

    with signed.open("rb") as stream:
        reader = PdfFileReader(stream)
        assert reader.encrypted
        assert reader.decrypt("RecipientSecret!").status.name == "USER"
        signatures = list(reader.embedded_signatures)
        assert len(signatures) == 1
        status = validation.validate_pdf_signature(
            signatures[0], signer_validation_context=ValidationContext(
                trust_roots=[], allow_fetching=False))
        assert status.intact and status.valid


def test_email1_rejects_password_placeholder_before_smtp(tmp_path):
    source = tmp_path / "doc.pdf"
    _make_test_pdf(source)
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(T.ToolError, match="cannot contain"):
        T.email_secure(work, [source], {
            "recipient_email": "client@example.com",
            "password_mode": "manual",
            "custom_password": "Secret123!",
            "email_body_html": "<p>Password: {{ password }}</p>",
        })


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
    assert "password" not in data
    assert data["password_included"] is False

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
    assert res["error"] == "The SMTP connection failed."
    assert res["diagnostic"]["stage"] == "password_delivery"


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

    with pytest.raises(smtp_manager.DeliveryUncertainError, match="did not confirm delivery"):
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
    assert "password" not in data
    assert "password" not in data["resend"]
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


@patch("smtplib.SMTP")
def test_send_dual_secure_email_multi_relay_failover(mock_smtp, tmp_path):
    primary = MagicMock()
    fallback = MagicMock()
    mock_smtp.side_effect = [primary, fallback]
    primary.ehlo.return_value = (250, b"ok")
    primary.sendmail.side_effect = smtplib.SMTPDataError(550, b"relay denied")
    fallback.ehlo.return_value = (250, b"ok")

    pdf_path = tmp_path / "doc.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(pdf_path)
    doc.close()

    res = smtp_manager.send_dual_secure_email(
        smtp=[
            {"server": "primary.relay.com", "port": 587, "username": "user@primary.com", "password": "pw1"},
            {"server": "backup.relay.com", "port": 587, "username": "user@backup.com", "password": "pw2"},
        ],
        recipient="client@example.com",
        pdf_path=pdf_path,
        password="MySecretKey123!",
        delay_seconds=0.05
    )

    assert res["ok"] is True
    assert "backup.relay.com" in res["relay_used"]
    assert fallback.sendmail.call_count == 2
    primary_message = message_from_string(primary.sendmail.call_args.args[2])
    fallback_message = message_from_string(fallback.sendmail.call_args_list[0].args[2])
    assert primary_message["Message-ID"] == fallback_message["Message-ID"]


@patch("smtplib.SMTP")
def test_email_secure_tool_includes_stego_tag_and_relay(mock_smtp, tmp_path):
    mock_inst = MagicMock()
    mock_smtp.return_value = mock_inst
    mock_inst.ehlo.return_value = (250, b"ok")

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
            "recipient_email": "vip@example.com",
            "password_mode": "manual",
            "custom_password": "MySecretPass999!",
            "mail_server": "smtp.example.com",
            "mail_port": "587",
            "mail_username": "sender@example.com",
            "mail_password": "pwd",
            "recipient_watermark": "1",
            "delay_seconds": "0.05",
        }
    )

    data = json.loads(result.path.read_text(encoding="utf-8"))
    assert data["status"] == "success"
    assert data["step1_sent"] is True
    assert data["step2_sent"] is True
    assert "stego_tag" in data
    assert len(data["stego_tag"]) == 32
    assert "smtp.example.com" in data["relay_used"]


def test_dispatch_delay_is_clamped():
    assert smtp_manager.clamp_dispatch_delay(-100) == 0.0
    assert smtp_manager.clamp_dispatch_delay(100000) == 10.0
    with pytest.raises(ValueError, match="finite"):
        smtp_manager.clamp_dispatch_delay(float("inf"))


def test_tls_context_requires_tls_1_2_or_newer():
    assert smtp_manager._tls_context().minimum_version == ssl.TLSVersion.TLSv1_2


@patch("smtplib.SMTP")
def test_smtp_relay_allowlist_rejects_unapproved_host_before_connect(mock_smtp, monkeypatch):
    monkeypatch.setenv("SMTP_ALLOWED_HOSTS", "approved.example.com")
    with pytest.raises(ValueError, match="not permitted by SMTP_ALLOWED_HOSTS"):
        smtp_manager._connect({
            "server": "unapproved.example.com", "port": 587,
            "security": "starttls", "username": "sender@example.com", "password": "pw",
        }, timeout=1)
    mock_smtp.assert_not_called()


@patch("smtplib.SMTP")
def test_smtp_relay_allowlist_rejects_unapproved_port_before_connect(mock_smtp, monkeypatch):
    monkeypatch.setenv("SMTP_ALLOWED_PORTS", "465")
    with pytest.raises(ValueError, match="not permitted by SMTP_ALLOWED_PORTS"):
        smtp_manager._connect({
            "server": "smtp.example.com", "port": 587,
            "security": "starttls", "username": "sender@example.com", "password": "pw",
        }, timeout=1)
    mock_smtp.assert_not_called()


@patch("smtplib.SMTP")
def test_failed_smtp_handshake_closes_socket(mock_smtp):
    server = mock_smtp.return_value
    server.ehlo.return_value = (500, b"EHLO rejected")
    with pytest.raises(smtplib.SMTPHeloError):
        smtp_manager._connect({
            "server": "smtp.example.com", "port": 587,
            "security": "starttls", "username": "sender@example.com", "password": "pw",
        }, timeout=1)
    server.close.assert_called_once_with()


def test_smtp_relay_pool_is_bounded(tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    _make_test_pdf(pdf_path)
    relays = [
        {"server": f"smtp-{idx}.example.com", "port": 587, "username": "sender@example.com", "password": "pw"}
        for idx in range(smtp_manager.MAX_SMTP_RELAYS + 1)
    ]
    with pytest.raises(ValueError, match="maximum of 3 SMTP relays"):
        smtp_manager.send_dual_secure_email(
            smtp=relays,
            recipient="client@example.com",
            pdf_path=pdf_path,
            password="MySecretKey123!",
        )


def test_email_html_sanitizer_removes_active_and_tracking_content():
    cleaned = smtp_manager.sanitize_email_html(
        '<p onclick="steal()">Hello <a href="javascript:bad()">link</a></p>'
        '<img src="https://tracker.invalid/pixel"><script>alert(1)</script>'
    )
    assert "onclick" not in cleaned
    assert "javascript:" not in cleaned
    assert "<img" not in cleaned
    assert "script" not in cleaned
    assert "Hello" in cleaned


def test_plaintext_smtp_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("SQUISH_ALLOW_PLAINTEXT_SMTP", raising=False)
    with pytest.raises(ValueError, match="Plaintext SMTP is disabled"):
        smtp_manager._connect({
            "server": "smtp.example.com", "port": 2525,
            "security": "none", "username": "sender@example.com", "password": "pw",
        }, timeout=1)
