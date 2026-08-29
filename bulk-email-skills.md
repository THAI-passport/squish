# Squish Bulk Secure Email — AI Generator Skill & Specification

This document provides exact guidelines, data structures, and template standards so any AI assistant can generate valid recipient data files (JSON, CSV, TSV) and personalized HTML email templates for **Squish Secure Dispatch**.

---

## 1. Supported Data Formats

Squish automatically detects and parses any of the following formats when pasted into the Bulk Recipients box or uploaded as a file:

### Format A: JSON Array (`.json`) — Recommended for Complex Data
```json
[
  {
    "email": "sandman69@gmail.com",
    "name": "Mr. Sand Man",
    "message": "Here is your confidential quarterly audit report."
  },
  {
    "email": "ironman555@gmail.com",
    "name": "Robert Stark",
    "message": "Please review the updated reactor blueprints attached."
  }
]
```

### Format B: CSV (`.csv`) — Standard Spreadsheet Export
```csv
email,name,message
sandman69@gmail.com,Mr. Sand Man,Here is your confidential quarterly audit report.
ironman555@gmail.com,Robert Stark,Please review the updated reactor blueprints attached.
```

### Format C: TSV (`.tsv` / Tab-Delimited) — Direct Copy-Paste from Excel/Sheets
```tsv
email	name	message
sandman69@gmail.com	Mr. Sand Man	Here is your confidential quarterly audit report.
ironman555@gmail.com	Robert Stark	Please review the updated reactor blueprints attached.
```

### Format D: Key-Value Text Format
```text
sandman69@gmail.com: "Mr. Sand Man" | "Here is your confidential quarterly audit report."
ironman555@gmail.com: "Robert Stark" | "Please review the updated reactor blueprints attached."
```

### Format E: Simple Email List (Comma or Newline Separated)
```text
sandman69@gmail.com, ironman555@gmail.com, spider616@gmail.com
```

---

## 2. Template Placeholders

When designing HTML email templates, subject lines, or Email #2 message bodies, use the following dynamic placeholder tags:

| Placeholder | Replaced With | Example Output |
| :--- | :--- | :--- |
| `{{name}}` | Recipient's full name | `Mr. Sand Man` |
| `{{email}}` | Recipient's email address | `sandman69@gmail.com` |
| `{{message}}` | Recipient's custom personal note | `Here is your confidential quarterly audit report.` |
| `{{doc_name}}` | Filename of the attached encrypted PDF | `Financial_Report_2026.pdf` |
| `{{password}}` | The individual AES-256 decryption key | `xK9#mQ2$vL5*` |

---

## 3. Best Practices for Replaceable HTML Email Templates

1. **Inline CSS Styling**: All styles must be inline on elements or in standard `<style>` blocks inside `<head>`.
2. **Table-Based Layout**: Use `<table role="presentation">` for cross-client compatibility (Gmail, Outlook, Apple Mail).
3. **Responsive Width**: Set `max-width: 600px; margin: 0 auto; width: 100%;`.
4. **Fallback Handling**: If `{{name}}` or `{{message}}` is empty for a recipient, Squish cleanly omits the tag without breaking formatting.

---

## 4. Example AI Prompt for Generating New Campaigns

To have an AI assistant generate both the recipient list and the HTML email template, you can prompt it with:

> *"Generate a recipient list in JSON format and a matching responsive HTML email template for Squish Secure Dispatch. The email is a [e.g. Confidential Board Briefing]. Include placeholders for `{{name}}`, `{{message}}`, and `{{doc_name}}`."*

---

## 5. Sample Full-Featured HTML Template

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Confidential Document Dispatch</title>
</head>
<body style="margin: 0; padding: 24px; background-color: #f8fafc; font-family: 'Segoe UI', Helvetica, Arial, sans-serif; color: #1e293b;">

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width: 600px; margin: 0 auto; background-color: #ffffff; border-radius: 10px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.05);">
    
    <!-- Header -->
    <tr>
      <td style="background: #0f172a; padding: 22px 28px; text-align: left;">
        <span style="font-size: 11px; font-weight: 700; letter-spacing: 2px; color: #94a3b8; text-transform: uppercase; display: block; margin-bottom: 4px;">
          SECURE DISPATCH SYSTEM
        </span>
        <h1 style="margin: 0; color: #ffffff; font-size: 20px; font-weight: 700;">
          Confidential Document Delivery
        </h1>
      </td>
    </tr>

    <!-- Body -->
    <tr>
      <td style="padding: 28px;">
        <p style="font-size: 16px; margin-top: 0; color: #0f172a;">
          Hello <strong>{{name}}</strong>,
        </p>

        <p style="color: #475569; font-size: 14.5px; line-height: 1.6;">
          Your protected PDF document <strong>{{doc_name}}</strong> has been encrypted with AES-256 and is attached to this transmission.
        </p>

        <!-- Dynamic Personal Message Box (if provided) -->
        <div style="background-color: #f1f5f9; border-left: 4px solid #3b82f6; border-radius: 4px; padding: 14px 18px; margin: 20px 0;">
          <p style="margin: 0; color: #1e293b; font-size: 13.5px; line-height: 1.5;">
            <strong>Note from sender:</strong><br>
            {{message}}
          </p>
        </div>

        <p style="color: #475569; font-size: 14px; line-height: 1.6;">
          For security compliance, the decryption password for this document will arrive in a separate email in a few moments.
        </p>
      </td>
    </tr>

    <!-- Footer -->
    <tr>
      <td style="background-color: #f8fafc; padding: 16px 28px; text-align: center; border-top: 1px solid #e2e8f0;">
        <p style="margin: 0; color: #94a3b8; font-size: 11.5px;">
          Sent to <strong>{{email}}</strong> via Squish Secure Dispatch.
        </p>
      </td>
    </tr>

  </table>

</body>
</html>
```
