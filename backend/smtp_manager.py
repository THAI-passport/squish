"""Squish SMTP Dispatcher

Handles SMTP connection testing and two-step secure document dispatch:
Email #1: Password-protected PDF attachment (+ optional HTML body).
Email #2: Decryption password notification.
"""

from __future__ import annotations

import base64
import os
import smtplib
import ssl
import time
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, Optional


def test_smtp_connection(smtp: Dict[str, Any]) -> Dict[str, Any]:
    """Test connection and authentication to the specified SMTP server."""
    host = smtp.get("server") or smtp.get("host")
    port = int(smtp.get("port") or 587)
    username = smtp.get("username")
    password = smtp.get("password")
    security = smtp.get("security") or ("ssl" if port == 465 else "starttls")

    if not host:
        return {"ok": False, "error": "SMTP server host is required"}

    try:
        if port == 465 or security == "ssl":
            context = ssl.create_default_context()
            server = smtplib.SMTP_SSL(host, port, context=context, timeout=15)
        else:
            server = smtplib.SMTP(host, port, timeout=15)

        with server:
            server.ehlo()
            if port != 465 and security != "ssl" and (security == "starttls" or port == 587):
                context = ssl.create_default_context()
                server.starttls(context=context)
                server.ehlo()

            if username and password:
                server.login(username, password)

        return {
            "ok": True,
            "message": f"Successfully connected and authenticated to {host}:{port}"
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def send_dual_secure_email(
    smtp: Dict[str, Any],
    recipient: str,
    pdf_path: Path,
    password: str,
    subject: Optional[str] = None,
    html_body: Optional[str] = None,
    email2_subject: Optional[str] = None,
    email2_body: Optional[str] = None,
    delay_seconds: float = 2.5
) -> Dict[str, Any]:
    """Sends Email #1 with protected PDF, then waits and sends Email #2 with password."""
    host = smtp.get("server") or smtp.get("host")
    port = int(smtp.get("port") or 587)
    username = smtp.get("username")
    password_auth = smtp.get("password")
    from_name = smtp.get("from_name") or ""
    security = smtp.get("security") or ("ssl" if port == 465 else "starttls")

    sender = username or smtp.get("sender") or "noreply@squish.local"
    from_header = f'"{from_name}" <{sender}>' if from_name else sender
    pdf_filename = pdf_path.name

    if not recipient:
        raise ValueError("Recipient email is required")

    # Connect to SMTP
    if port == 465 or security == "ssl":
        context = ssl.create_default_context()
        server = smtplib.SMTP_SSL(host, port, context=context, timeout=20)
    else:
        server = smtplib.SMTP(host, port, timeout=20)

    try:
        server.ehlo()
        if port != 465 and security != "ssl" and (security == "starttls" or port == 587):
            context = ssl.create_default_context()
            server.starttls(context=context)
            server.ehlo()

        if username and password_auth:
            server.login(username, password_auth)

        # ------------------------------------------------------------- EMAIL 1 ---
        msg1 = MIMEMultipart("mixed")
        msg1["From"] = from_header
        msg1["To"] = recipient
        msg1["Subject"] = f"[Secure Document] {subject}" if subject else f"[Secure Document] Attached: {pdf_filename}"
        msg1["Date"] = smtplib.email.utils.formatdate(localtime=True)

        alt1 = MIMEMultipart("alternative")
        plain_text1 = (
            f"You have received an encrypted PDF document: {pdf_filename}.\n\n"
            "This file is password-protected for security. "
            "The decryption password will be sent in a separate email shortly."
        )

        if html_body and ("<" in html_body and ">" in html_body):
            html_text1 = html_body
        elif html_body:
            # Wrap plain text in clean HTML
            html_paragraphs = "".join(f"<p>{line}</p>" for line in html_body.split("\n") if line.strip())
            html_text1 = (
                '<div style="font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, sans-serif; '
                'max-width: 600px; margin: 0 auto; padding: 24px; border: 1px solid #e5e3dc; border-radius: 8px; background: #ffffff;">'
                f'<h2 style="color: #1b1d23; margin-top: 0;">{subject or "Secure Document Attached"}</h2>'
                f'{html_paragraphs}'
                '<div style="background: #f7f6f3; padding: 14px 18px; border-radius: 6px; margin: 18px 0; border-left: 4px solid #2f5fd8;">'
                f'<p style="margin: 0; color: #1b1d23; font-size: 14px;"><strong>Attached:</strong> {pdf_filename}</p>'
                '<p style="margin: 4px 0 0; color: #5c6070; font-size: 13px;">This document is AES-256 encrypted. The decryption key will arrive in a separate email shortly.</p>'
                '</div>'
                '</div>'
            )
        else:
            html_text1 = (
                '<div style="font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, sans-serif; '
                'max-width: 600px; margin: 0 auto; padding: 24px; border: 1px solid #e5e3dc; border-radius: 8px; background: #ffffff;">'
                f'<h2 style="color: #1b1d23; margin-top: 0;">Secure Document Attached</h2>'
                f'<p style="color: #33363f; line-height: 1.5;">You have received an encrypted PDF document: '
                f'<strong>{pdf_filename}</strong>.</p>'
                '<div style="background: #f7f6f3; padding: 14px 18px; border-radius: 6px; margin: 18px 0; border-left: 4px solid #2f5fd8;">'
                '<p style="margin: 0; color: #1b1d23; font-size: 14px;"><strong>Note:</strong> '
                'This document is password-protected for your security. '
                'The decryption key is being transmitted in a separate message shortly.</p>'
                '</div>'
                '<p style="color: #5c6070; font-size: 13px;">Dispatched via Squish Secure Dispatch.</p>'
                '</div>'
            )

        alt1.attach(MIMEText(plain_text1, "plain", "utf-8"))
        alt1.attach(MIMEText(html_text1, "html", "utf-8"))
        msg1.attach(alt1)

        # Attach PDF
        with pdf_path.open("rb") as f:
            pdf_part = MIMEApplication(f.read(), _subtype="pdf")
            pdf_part.add_header("Content-Disposition", "attachment", filename=pdf_filename)
            msg1.attach(pdf_part)

        server.sendmail(sender, [recipient], msg1.as_string())

        # ------------------------------------------------------------- DELAY ---
        time.sleep(max(0.5, float(delay_seconds)))

        # ------------------------------------------------------------- EMAIL 2 ---
        msg2 = MIMEMultipart("alternative")
        msg2["From"] = from_header
        msg2["To"] = recipient
        
        # Email 2 Subject
        e2_subj_template = email2_subject or f"[Decryption Key] Password for: {subject or pdf_filename}"
        e2_subj = e2_subj_template.replace("{{doc_name}}", pdf_filename).replace("{{filename}}", pdf_filename)
        msg2["Subject"] = e2_subj
        msg2["Date"] = smtplib.email.utils.formatdate(localtime=True)

        if email2_body:
            rendered_custom_e2 = email2_body.replace("{{password}}", password).replace("{{doc_name}}", pdf_filename).replace("{{filename}}", pdf_filename)
            if "<" in rendered_custom_e2 and ">" in rendered_custom_e2:
                html_text2 = rendered_custom_e2
                plain_text2 = f"Decryption Key: {password}\nFile: {pdf_filename}"
            else:
                html_paras = "".join(f"<p>{line}</p>" for line in rendered_custom_e2.split("\n") if line.strip())
                html_text2 = (
                    '<div style="font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, sans-serif; '
                    'max-width: 600px; margin: 0 auto; padding: 24px; border: 1px solid #e5e3dc; border-radius: 8px; background: #ffffff;">'
                    f'{html_paras}'
                    '</div>'
                )
                plain_text2 = rendered_custom_e2
        else:
            plain_text2 = (
                f"Your password to open the protected PDF ({pdf_filename}) is:\n\n"
                f"{password}\n\n"
                "Please keep this password secure."
            )
            html_text2 = (
                '<div style="font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, sans-serif; '
                'max-width: 600px; margin: 0 auto; padding: 24px; border: 1px solid #e5e3dc; border-radius: 8px; background: #ffffff;">'
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
                '</div>'
            )

        msg2.attach(MIMEText(plain_text2, "plain", "utf-8"))
        msg2.attach(MIMEText(html_text2, "html", "utf-8"))

        server.sendmail(sender, [recipient], msg2.as_string())

        return {
            "ok": True,
            "recipient": recipient,
            "pdf_filename": pdf_filename,
            "message": f"Successfully delivered dual secure emails to {recipient}"
        }
    finally:
        try:
            server.quit()
        except Exception:
            pass
