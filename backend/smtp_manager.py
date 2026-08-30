"""Squish SMTP Dispatcher

Handles SMTP connection testing and two-step secure document dispatch:
Email #1: Password-protected PDF attachment (+ optional HTML body).
Email #2: Decryption password notification.
"""

from __future__ import annotations

import re
import smtplib
import ssl
import time
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid, parseaddr
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

MAX_ATTACHMENTS = 10
MAX_TOTAL_ATTACHMENT_BYTES = 12 * 1024 * 1024

# ------------------------------------------------------------ sanitizers ---
# Email headers are newline-delimited (RFC 5322). Any \r or \n coming from a
# user-controlled field (subject, recipient, from_name, filename, ...) that
# reaches `msg["Header"] = value` unescaped lets an attacker splice in extra
# headers (e.g. an extra Bcc:) or break the MIME structure entirely. Strip
# control characters from every such field before it touches a header.
_HEADER_STRIP_RE = re.compile(r'[\r\n\x00]')


def sanitize_header_value(val: Any) -> str:
    """Strip CR/LF/NUL so a value can't inject extra email headers."""
    if val is None:
        return ""
    return _HEADER_STRIP_RE.sub('', str(val)).strip()


def sanitize_filename_header(name: Any) -> str:
    """Sanitize a value used in a Content-Disposition filename."""
    cleaned = sanitize_header_value(name)
    cleaned = cleaned.replace('"', "'").replace('\\', '_').replace('/', '_')
    return cleaned or "document.pdf"


def validate_recipient_email(email_str: Any) -> str:
    """Return a single well-formed recipient address, or raise ValueError.

    Rejects anything containing a comma/semicolon (multiple addresses) or
    control characters, and requires `email.utils.parseaddr` to extract a
    plausible `user@host` address with no embedded whitespace.
    """
    cleaned = sanitize_header_value(email_str)
    if not cleaned or ',' in cleaned or ';' in cleaned:
        raise ValueError("Recipient must be a single, valid email address")
    _, addr = parseaddr(cleaned)
    if not addr or '@' not in addr or ' ' in addr or addr != cleaned.strip('<>'):
        # parseaddr is lenient (e.g. it accepts "not an email" -> ('', ''));
        # require the parsed address to match what was given, modulo angle
        # brackets, so junk like "a b@c.com" or unparsable strings are caught.
        if not addr or '@' not in addr or ' ' in addr:
            raise ValueError(f"Invalid recipient email address: {email_str!r}")
    return addr


def _html_wrapper(inner: str) -> str:
    return (
        '<div style="font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, sans-serif; '
        'max-width: 600px; margin: 0 auto; padding: 24px; border: 1px solid #e5e3dc; border-radius: 8px; background: #ffffff;">'
        f'{inner}'
        '</div>'
    )


def _connect(smtp: Dict[str, Any], timeout: float) -> smtplib.SMTP:
    host = smtp.get("server") or smtp.get("host")
    port = int(smtp.get("port") or 587)
    username = smtp.get("username")
    password_auth = smtp.get("password")
    security = smtp.get("security") or ("ssl" if port == 465 else "starttls")

    if port == 465 or security == "ssl":
        context = ssl.create_default_context()
        server = smtplib.SMTP_SSL(host, port, context=context, timeout=timeout)
    else:
        server = smtplib.SMTP(host, port, timeout=timeout)

    server.ehlo()
    if port != 465 and security != "ssl" and (security == "starttls" or port == 587):
        context = ssl.create_default_context()
        server.starttls(context=context)
        server.ehlo()

    if username and password_auth:
        server.login(username, password_auth)

    return server


def test_smtp_connection(smtp: Dict[str, Any]) -> Dict[str, Any]:
    """Test connection and authentication to the specified SMTP server."""
    host = smtp.get("server") or smtp.get("host")
    if not host:
        return {"ok": False, "error": "SMTP server host is required"}

    port = int(smtp.get("port") or 587)
    try:
        server = _connect(smtp, timeout=15)
        try:
            server.quit()
        except Exception:
            pass
        return {
            "ok": True,
            "message": f"Successfully connected and authenticated to {host}:{port}"
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _build_email2(
    from_header: str,
    recipient: str,
    pdf_filename: str,
    password: str,
    subject: Optional[str],
    email2_subject: Optional[str],
    email2_body: Optional[str],
    in_reply_to: Optional[str] = None,
) -> MIMEMultipart:
    msg2 = MIMEMultipart("alternative")
    msg2["From"] = from_header
    msg2["To"] = recipient

    if in_reply_to:
        msg2["In-Reply-To"] = in_reply_to
        msg2["References"] = in_reply_to
        if not email2_subject:
            base_subj = subject or pdf_filename
            e2_subj_template = f"Re: [Secure Document] {base_subj}" if not base_subj.lower().startswith("re:") else base_subj
        else:
            e2_subj_template = email2_subject
    else:
        e2_subj_template = email2_subject or f"[Decryption Key] Password for: {subject or pdf_filename}"

    e2_subj = sanitize_header_value(
        e2_subj_template.replace("{{doc_name}}", pdf_filename).replace("{{filename}}", pdf_filename)
    )
    msg2["Subject"] = e2_subj
    msg2["Date"] = formatdate(localtime=True)

    if email2_body:
        rendered_custom_e2 = email2_body.replace("{{password}}", password).replace("{{doc_name}}", pdf_filename).replace("{{filename}}", pdf_filename)
        if "<" in rendered_custom_e2 and ">" in rendered_custom_e2:
            html_text2 = rendered_custom_e2
            plain_text2 = f"Decryption Key: {password}\nFile: {pdf_filename}"
        else:
            html_paras = "".join(f"<p>{line}</p>" for line in rendered_custom_e2.split("\n") if line.strip())
            html_text2 = _html_wrapper(html_paras)
            plain_text2 = rendered_custom_e2
    else:
        plain_text2 = (
            f"Your password to open the protected PDF ({pdf_filename}) is:\n\n"
            f"{password}\n\n"
            "Please keep this password secure."
        )
        html_text2 = _html_wrapper(
            '<h2 style="color: #1b1d23; margin-top: 0;">Your Document Decryption Key</h2>'
            '<p style="color: #33363f; line-height: 1.5;">Use the password below to open the encrypted PDF '
            f'document (<strong>{pdf_filename}</strong>) you recently received:</p>'
            '<div style="background: #efeee9; padding: 16px 20px; border-radius: 6px; margin: 20px 0; '
            'text-align: center; border: 1px solid #d4d1c8;">'
            f'<span style="font-family: monospace; font-size: 20px; font-weight: 700; color: #1b1d23; '
            f'letter-spacing: 2px;">{password}</span>'
            '</div>'
            '<p style="color: #5c6070; font-size: 13px;">Please keep this password secure and do not forward it '
            'alongside the encrypted document.</p>'
        )

    msg2.attach(MIMEText(plain_text2, "plain", "utf-8"))
    msg2.attach(MIMEText(html_text2, "html", "utf-8"))
    return msg2


def _normalise_pdf_paths(pdf_path: Path | Iterable[Path]) -> list[Path]:
    """Accept the legacy single path and the new attachment list contract."""
    if isinstance(pdf_path, Path):
        paths = [pdf_path]
    else:
        paths = [Path(p) for p in pdf_path]
    if not paths:
        raise ValueError("At least one encrypted PDF attachment is required")
    if len(paths) > MAX_ATTACHMENTS:
        raise ValueError(f"A maximum of {MAX_ATTACHMENTS} PDF attachments is allowed")
    missing = [p.name for p in paths if not p.is_file()]
    if missing:
        raise ValueError(f"Encrypted attachment not found: {', '.join(missing)}")
    total_bytes = sum(p.stat().st_size for p in paths)
    if total_bytes > MAX_TOTAL_ATTACHMENT_BYTES:
        raise ValueError("Encrypted PDF attachments exceed the 12 MB combined limit")
    return paths


def send_key_notification(
    smtp: Dict[str, Any],
    recipient: str,
    password: str,
    pdf_filename: str,
    subject: Optional[str] = None,
    email2_subject: Optional[str] = None,
    email2_body: Optional[str] = None,
) -> Dict[str, Any]:
    """Send just the decryption-key email (Email #2) on its own.

    Used for "Resend Decryption Key" after a partial failure -- reuses the
    exact password that was already generated/emailed to Email #1's
    recipient's inbox as the PDF, without re-encrypting anything.
    """
    recipient = validate_recipient_email(recipient)
    from_name = sanitize_header_value(smtp.get("from_name") or "")
    sender = sanitize_header_value(smtp.get("username") or smtp.get("sender") or "noreply@squish.local")
    pdf_filename = sanitize_filename_header(pdf_filename)
    subject = sanitize_header_value(subject) if subject else None
    email2_subject = sanitize_header_value(email2_subject) if email2_subject else None
    from_header = f'"{from_name}" <{sender}>' if from_name else sender

    server = _connect(smtp, timeout=20)
    try:
        msg2 = _build_email2(from_header, recipient, pdf_filename, password, subject, email2_subject, email2_body)
        server.sendmail(sender, [recipient], msg2.as_string())
        return {"ok": True, "recipient": recipient, "message": f"Decryption key resent to {recipient}"}
    finally:
        try:
            server.quit()
        except Exception:
            pass


def send_dual_secure_email(
    smtp: Dict[str, Any] | list[Dict[str, Any]],
    recipient: str,
    pdf_path: Path | Iterable[Path],
    password: str,
    subject: Optional[str] = None,
    html_body: Optional[str] = None,
    email2_subject: Optional[str] = None,
    email2_body: Optional[str] = None,
    delay_seconds: float = 2.5,
    thread_emails: bool = False,
    key_delivery_mode: str = "email",
    plain_text_only: bool = False,
    max_retries_per_relay: int = 2,
) -> Dict[str, Any]:
    """Send encrypted PDFs and optionally deliver their password by email with multi-relay fallback.

    Supports single SMTP config or prioritized multi-relay pool.
    """
    import random

    recipient = validate_recipient_email(recipient)
    pdf_paths = _normalise_pdf_paths(pdf_path)
    mode = str(key_delivery_mode or "email").lower()
    if mode not in {"email", "oob", "dual"}:
        raise ValueError("key_delivery_mode must be email, oob, or dual")

    pool: list[Dict[str, Any]] = smtp if isinstance(smtp, list) else [smtp]
    if not pool or not any(p.get("server") or p.get("host") for p in pool):
        raise ValueError("At least one valid SMTP profile is required")

    pdf_filenames = [sanitize_filename_header(path.name) for path in pdf_paths]
    pdf_filename = pdf_filenames[0]
    attachment_label = pdf_filename if len(pdf_filenames) == 1 else f"{len(pdf_filenames)} encrypted PDF files"
    subject = sanitize_header_value(subject) if subject else None
    email2_subject = sanitize_header_value(email2_subject) if email2_subject else None

    last_exc = None
    total_retries = 0

    for pool_idx, current_smtp in enumerate(pool):
        from_name = sanitize_header_value(current_smtp.get("from_name") or "")
        username = current_smtp.get("username")
        sender = sanitize_header_value(username or current_smtp.get("sender") or "noreply@squish.local")
        from_header = f'"{from_name}" <{sender}>' if from_name else sender
        relay_host = current_smtp.get("server") or current_smtp.get("host") or "smtp"

        for attempt in range(max_retries_per_relay + 1):
            server = None
            step1_sent = False
            try:
                server = _connect(current_smtp, timeout=20)

                # ------------------------------------------------------------- EMAIL 1 ---
                msg1 = MIMEMultipart("mixed")
                msg1["From"] = from_header
                msg1["To"] = recipient
                msg1["Subject"] = sanitize_header_value(
                    f"[Secure Document] {subject}" if subject else f"[Secure Document] Attached: {attachment_label}"
                )
                msg1["Date"] = formatdate(localtime=True)
                msg1_id = make_msgid(domain=sender.split("@")[-1] if "@" in sender else None)
                msg1["Message-ID"] = msg1_id

                alt1 = MIMEMultipart("alternative")
                delivery_note = (
                    "The decryption password will be delivered through a separate channel."
                    if mode == "oob"
                    else "The decryption password will be sent in a separate email shortly."
                )
                plain_text1 = html_body if plain_text_only and html_body else (
                    f"You have received {attachment_label}.\n\n"
                    f"The attached files are password-protected for security. {delivery_note}"
                )

                if html_body and ("<" in html_body and ">" in html_body):
                    html_text1 = html_body
                elif html_body:
                    html_paragraphs = "".join(f"<p>{line}</p>" for line in html_body.split("\n") if line.strip())
                    html_text1 = _html_wrapper(
                        f'<h2 style="color: #1b1d23; margin-top: 0;">{subject or "Secure Document Attached"}</h2>'
                        f'{html_paragraphs}'
                        '<div style="background: #f7f6f3; padding: 14px 18px; border-radius: 6px; margin: 18px 0; border-left: 4px solid #2f5fd8;">'
                        f'<p style="margin: 0; color: #1b1d23; font-size: 14px;"><strong>Attached:</strong> {attachment_label}</p>'
                        f'<p style="margin: 4px 0 0; color: #5c6070; font-size: 13px;">{delivery_note}</p>'
                        '</div>'
                    )
                else:
                    html_text1 = _html_wrapper(
                        '<h2 style="color: #1b1d23; margin-top: 0;">Secure Document Attached</h2>'
                        f'<p style="color: #33363f; line-height: 1.5;">You have received '
                        f'<strong>{attachment_label}</strong>.</p>'
                        '<div style="background: #f7f6f3; padding: 14px 18px; border-radius: 6px; margin: 18px 0; border-left: 4px solid #2f5fd8;">'
                        '<p style="margin: 0; color: #1b1d23; font-size: 14px;"><strong>Note:</strong> '
                        f'The attached files are password-protected for your security. {delivery_note}</p>'
                        '</div>'
                        '<p style="color: #5c6070; font-size: 13px;">Dispatched via Squish Secure Dispatch.</p>'
                    )

                if plain_text_only:
                    msg1.attach(MIMEText(plain_text1, "plain", "utf-8"))
                else:
                    alt1.attach(MIMEText(plain_text1, "plain", "utf-8"))
                    alt1.attach(MIMEText(html_text1, "html", "utf-8"))
                    msg1.attach(alt1)

                for path, filename in zip(pdf_paths, pdf_filenames):
                    with path.open("rb") as f:
                        pdf_part = MIMEApplication(f.read(), _subtype="pdf")
                    pdf_part.add_header("Content-Disposition", "attachment", filename=filename)
                    msg1.attach(pdf_part)

                server.sendmail(sender, [recipient], msg1.as_string())
                step1_sent = True

                if mode in {"email", "dual"}:
                    # --------------------------------------------------------- DELAY ---
                    time.sleep(max(0.5, float(delay_seconds)))

                    # --------------------------------------------------------- EMAIL 2 ---
                    msg2 = _build_email2(
                        from_header, recipient, attachment_label, password, subject,
                        email2_subject, email2_body,
                        in_reply_to=msg1_id if thread_emails else None
                    )
                    server.sendmail(sender, [recipient], msg2.as_string())

                relay_label = relay_host if pool_idx == 0 else f"{relay_host} (fallback from {pool[0].get('server', 'primary')})"
                return {
                    "ok": True,
                    "partial": False,
                    "step1_sent": True,
                    "step2_sent": mode in {"email", "dual"},
                    "recipient": recipient,
                    "pdf_filename": pdf_filename,
                    "attachments": pdf_filenames,
                    "key_delivery_mode": mode,
                    "oob_required": mode in {"oob", "dual"},
                    "relay_used": relay_label,
                    "retries": total_retries,
                    "message": (
                        f"Successfully delivered encrypted attachments to {recipient}; "
                        "password awaits out-of-band delivery"
                        if mode == "oob"
                        else f"Successfully delivered secure email to {recipient}"
                    )
                }
            except Exception as exc:
                last_exc = exc
                if step1_sent:
                    # PDF was sent; password email failed. Return partial status.
                    return {
                        "ok": False,
                        "partial": True,
                        "step1_sent": True,
                        "step2_sent": False,
                        "recipient": recipient,
                        "pdf_filename": pdf_filename,
                        "attachments": pdf_filenames,
                        "key_delivery_mode": mode,
                        "relay_used": relay_host,
                        "retries": total_retries,
                        "error": str(exc),
                        "message": f"PDF delivered to {recipient}, but the password email failed: {exc}"
                    }
                # Check if transient error eligible for retry on same relay
                is_transient = isinstance(exc, (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, TimeoutError)) or (
                    isinstance(exc, smtplib.SMTPResponseException) and exc.smtp_code in (421, 450, 451)
                )
                if is_transient and attempt < max_retries_per_relay:
                    total_retries += 1
                    time.sleep(random.uniform(1.0, 2.5))
                    continue
                # Relay failed; break retry loop to try next relay in pool
                break
            finally:
                if server is not None:
                    try:
                        server.quit()
                    except Exception:
                        pass

    # If we reached here, all relays in pool failed
    if last_exc:
        raise last_exc
    raise ValueError("All configured SMTP relays failed to send")
