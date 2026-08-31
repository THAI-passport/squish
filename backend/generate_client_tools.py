import json
import os
import re
import io
import sys
import contextlib

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

# Importing tools pulls in PyMuPDF, which prints a
#   "warning: The fitz API is deprecated ... Use `import pymupdf` instead"
# banner to STDOUT on `import fitz`. If this generator's stdout is ever
# redirected into a file (e.g. `python generate_client_tools.py > tools.json`),
# that banner ends up as the first line and corrupts the JSON. Swallow anything
# the import prints so every artifact we emit is clean regardless of how the
# generator is invoked.
_import_noise = io.StringIO()
with contextlib.redirect_stdout(_import_noise), contextlib.redirect_stderr(_import_noise):
    import tools

# Apply static-mode availability.
#
# Two reasons a tool cannot run client-side in Pyodide:
#   1. It shells out to a native CLI binary (there is no subprocess in the
#      browser), or
#   2. It imports a Python package that has no usable wasm build
#      (native system dependencies).
#
# IMPORTANT: keep this map in sync with tools.py. Any NEW tool that calls
# run([...]) or imports weasyprint / pyhanko / another non-wasm package MUST
# be added here, otherwise it renders as available in the UI and then throws
# "Local processing failed" at runtime. The value is shown to the user as
# the reason the tool is greyed out.
unsupported = {
    # -- shell out to a native binary (no subprocess in Pyodide) --
    "pdf-to-pdfa":      "Ghostscript",   # gs
    "office-to-pdf":    "LibreOffice",   # soffice
    "pdf-to-word":      "pdf2docx",      # native deps, not wasm-safe
    # -- import a package with no usable wasm build --
    "sign-pdf":         "pyhanko",       # crypto stack, not Pyodide-installable
    "verify-signature": "pyhanko",
    # Enabled dynamically only when the Cloudflare Worker answers its
    # capability probe. Plain static hosts keep showing this as unavailable.
    "email-secure":     "Cloudflare email worker",
}

# Native server implementations can have an intentionally narrower browser
# implementation. The run wrapper swaps only these functions; the canonical
# registry and the FastAPI backend keep using the stronger native engines.
static_fn_overrides = {
    "compress": "compress_wasm",
    "repair": "repair_wasm",
    "grayscale": "grayscale_wasm",
    "md-to-pdf": "md_to_pdf_wasm",
}

# Native registry entries that are replaced by a non-Python implementation in
# client_tools_worker.template.js. Unlike static_fn_overrides, these handlers
# deliberately bypass tools.py entirely.
static_worker_handlers = {"ocr"}

static_blurb_overrides = {
    "compress": "Clean, deduplicate and deflate PDF objects without uploading the file.",
    "repair": "Best-effort recovery of damaged PDF structure, entirely in your browser.",
    "grayscale": "Rebuild pages in grayscale for cheaper printing (browser mode rasterises pages).",
    "md-to-pdf": "Render Markdown to PDF locally with browser-safe page layout.",
    "ocr": "Recognise scans in a background WASM worker and add an invisible searchable text layer without uploading the PDF.",
}

# Two shapes are built from one pass over the registry:
#   registry_data  -> the raw tool registry, written to tools.json (valid,
#                     pretty, no environment-specific fields). This is the
#                     human-readable source of truth.
#   static_data    -> the same tools plus static-mode availability, inlined
#                     into client_tools.js for the Pyodide fallback.
registry_data = []
static_data = []
for t in tools.TOOLS:
    d = {k: v for k, v in vars(t).items() if not callable(v)}
    registry_data.append(dict(d))

    s = dict(d)
    if s["key"] in static_blurb_overrides:
        s["blurb"] = static_blurb_overrides[s["key"]]
    if s["key"] in unsupported:
        s["available"] = False
        s["needs"] = unsupported[s["key"]]
    else:
        s["available"] = True
        s["needs"] = ""
    static_data.append(s)

tools_json = json.dumps(static_data, separators=(',', ':'))

# Pull the real build version from app.py so the static-mode badge is honest
# rather than a hardcoded 'v1.0'.
app_version = "unknown"
try:
    with open(os.path.join(os.path.dirname(__file__), 'app.py')) as _f:
        _m = re.search(r'VERSION\s*=\s*["\']([^"\']+)["\']', _f.read())
        if _m:
            app_version = _m.group(1)
except OSError:
    pass

# Read tools.py content
with open(os.path.join(os.path.dirname(__file__), 'tools.py'), 'r') as f:
    tools_py_content = f.read()

js_content = f"""
// Auto-generated client_tools.js for static WASM fallback
const STATIC_TOOLS = {tools_json};
const STATIC_VERSION = "{app_version}";
window.STATIC_VERSION = STATIC_VERSION;
const STATIC_FN_OVERRIDES = {json.dumps(static_fn_overrides, separators=(',', ':'))};

// All Python/WASM work runs off the UI thread. Files are structured-cloned as
// Blob handles, then staged to OPFS inside the worker without a whole-file JS
// ArrayBuffer on the main thread.
let processingWorker = null;
let processingSequence = 0;
const processingJobs = new Map();
let tesseractModule = null;
let tesseractWorker = null;
let tesseractLanguage = '';

async function recogniseOcrPage(message) {{
  try {{
    if (!tesseractModule) tesseractModule = await import('/vendor/tesseract/tesseract.esm.min.js');
    if (!tesseractWorker || tesseractLanguage !== message.lang) {{
      if (tesseractWorker) await tesseractWorker.terminate();
      const status = document.getElementById('fileStatus');
      if (status) status.textContent = `Loading ${{message.lang}} OCR model (cached after first use)…`;
      const tesseract = tesseractModule.createWorker ? tesseractModule : tesseractModule.default;
      tesseractWorker = await tesseract.createWorker(message.lang, 1, {{
        workerPath:'/vendor/tesseract/worker.min.js',
        corePath:'/vendor/tesseract/core',
        langPath:'/vendor/tesseract/lang',
        cachePath:'squish-ocr-v1',
        workerBlobURL:false,
        errorHandler:error => console.error('OCR worker error', error),
        logger: update => {{
          if (status && update.status && Number.isFinite(update.progress)) status.textContent = `${{update.status}} · ${{Math.round(update.progress * 100)}}%`;
        }},
      }});
      tesseractLanguage = message.lang;
    }}
    const result = await tesseractWorker.recognize(message.image, {{}}, {{text:false, blocks:false, tsv:true}});
    processingWorker?.postMessage({{type:'ocr-result', requestId:message.requestId, tsv:result.data.tsv || ''}});
  }} catch (error) {{
    processingWorker?.postMessage({{type:'ocr-result', requestId:message.requestId, error:error instanceof Error ? error.message : String(error)}});
  }}
}}

function ensureProcessingWorker() {{
  if (processingWorker) return processingWorker;
  processingWorker = new Worker('/client_tools_worker.js?v={app_version}', {{type:'module'}});
  processingWorker.onmessage = event => {{
    const message = event.data || {{}};
    if (message.type === 'ocr-page') {{ recogniseOcrPage(message); return; }}
    const job = processingJobs.get(message.id);
    if (!job) return;
    if (message.type === 'progress') {{
      const status = document.getElementById('fileStatus');
      if (status) status.textContent = message.message || 'Processing locally…';
      return;
    }}
    if (message.type === 'result') {{
      job.result = {{
        blob:message.blob, name:message.name, storage:message.storage,
        verifiedText:message.verifiedText || ''
      }};
      return;
    }}
    processingJobs.delete(message.id);
    if (message.type === 'cleanup') job.resolve({{...job.result, cleaned:true}});
    else job.reject(new Error(message.error || 'Local processing failed'));
  }};
  processingWorker.onerror = error => {{
    for (const job of processingJobs.values()) job.reject(new Error(error.message || 'Browser worker stopped'));
    processingJobs.clear();
    processingWorker?.terminate();
    processingWorker = null;
  }};
  return processingWorker;
}}

window.runPyodideTool = function(key, files, formData) {{
  const id = `squish-${{Date.now()}}-${{++processingSequence}}`;
  const params = {{}};
  for (const [name, value] of formData.entries()) if (name !== 'files') params[name] = value;
  params.__static_fn_overrides = JSON.stringify(STATIC_FN_OVERRIDES);
  return new Promise((resolve, reject) => {{
    processingJobs.set(id, {{resolve, reject}});
    ensureProcessingWorker().postMessage({{type:'run', id, key, files:Array.from(files), params}});
  }});
}};

window.cancelLocalProcessing = function() {{
  if (!processingWorker) return;
  processingWorker.terminate();
  processingWorker = null;
  for (const job of processingJobs.values()) job.reject(new Error('Processing cancelled'));
  processingJobs.clear();
  if (tesseractWorker) {{ tesseractWorker.terminate(); tesseractWorker = null; tesseractLanguage = ''; }}
}};

function staticRandomSecret(bytes = 16) {{
  const raw = new Uint8Array(bytes);
  crypto.getRandomValues(raw);
  return Array.from(raw, b => b.toString(16).padStart(2, '0')).join('');
}}

async function blobToBase64(blob) {{
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let binary = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {{
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }}
  return btoa(binary);
}}

window.runStaticSecureEmail = async function(files, formData) {{
  let pdfs = files.filter(f => /\\.pdf$/i.test(f.name));
  if (!pdfs.length) throw new Error('Secure Email Dispatch needs at least one PDF file.');
  if (pdfs.length > 10) throw new Error('A maximum of 10 PDF attachments is allowed.');
  const password = String(formData.get('custom_password') || '').trim();
  if (password.length < 4) throw new Error('Password must be at least 4 characters.');

  const dispatchPages = String(formData.get('dispatch_pages') || '').trim();
  if (dispatchPages) {{
    if (pdfs.length !== 1) throw new Error('Split & Dispatch accepts exactly one master PDF.');
    const splitData = new FormData();
    splitData.append('pages', dispatchPages);
    const selected = await window.runPyodideTool('__dispatch-extract', [pdfs[0]], splitData);
    pdfs = [new File([selected.blob], selected.name, {{type: 'application/pdf'}})];
  }}

  const protectedResults = [];
  for (const pdf of pdfs) {{
    const protectData = new FormData();
    protectData.append('password_new', password);
    protectData.append('owner_password', staticRandomSecret());
    protectedResults.push(await window.runPyodideTool('protect', [pdf], protectData));
  }}

  let smtp = {{}};
  const saved = formData.get('smtp_profile_json');
  if (saved) {{
    try {{ smtp = JSON.parse(saved); }} catch {{ throw new Error('Saved SMTP profile is invalid.'); }}
  }} else {{
    smtp = {{
      server: String(formData.get('mail_server') || ''),
      port: Number(formData.get('mail_port') || 587),
      username: String(formData.get('mail_username') || ''),
      password: String(formData.get('mail_password') || ''),
      from_name: String(formData.get('mail_from_name') || ''),
      security: String(formData.get('mail_security') || 'starttls')
    }};
  }}
  if (!smtp.server || !smtp.username || !smtp.password) {{
    throw new Error('SMTP server, sender email, and SMTP password are required.');
  }}

  const htmlFile = files.find(f => /\\.(html?|txt)$/i.test(f.name));
  const customHtml = String(formData.get('email_body_html') || '');
  if (customHtml.replace(/\\s/g, '').toLowerCase().includes('{{{{password}}}}')) {{
    throw new Error('Email #1 templates cannot contain {{password}}.');
  }}
  let resolvedHtml = customHtml;
  if (!resolvedHtml && htmlFile) {{
    try {{
      resolvedHtml = await htmlFile.text();
    }} catch(e) {{
      resolvedHtml = await new Promise((resolve) => {{
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => resolve('');
        reader.readAsText(htmlFile);
      }});
    }}
  }}
  const requestedKeyDeliveryMode = String(formData.get('key_delivery_mode') || 'email');
  const payload = {{
    smtp,
    recipient: String(formData.get('recipient_email') || ''),
    subject: String(formData.get('email_subject') || ''),
    attachments: await Promise.all(protectedResults.map(async result => ({{
      base64: await blobToBase64(result.blob), filename: result.name
    }}))),
    htmlBody: resolvedHtml,
    plainTextOnly: formData.get('email_plain_text_only') === '1',
    password,
    delaySeconds: Number(formData.get('delay_seconds') || 2.5),
    email2Subject: String(formData.get('email2_subject') || ''),
    email2Body: String(formData.get('email2_body') || ''),
    threadEmails: formData.get('thread_emails') === '1' || formData.get('thread_emails') === 'true',
    // The link secret must never be sent to the SMTP Worker. Burner mode sends
    // the encrypted PDF first, then creates the zero-knowledge link locally.
    keyDeliveryMode: requestedKeyDeliveryMode === 'burner' ? 'oob' : requestedKeyDeliveryMode
  }};

  const response = await fetch('/api/t/email-secure', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify(payload)
  }});
  let receipt;
  try {{ receipt = await response.json(); }} catch {{ throw new Error(`Email worker returned HTTP ${{response.status}}.`); }}
  if (!response.ok && receipt.status !== 'partial_failure') {{
    throw new Error(receipt.error || `Email worker returned HTTP ${{response.status}}.`);
  }}
  if (requestedKeyDeliveryMode === 'burner' && receipt.status !== 'partial_failure') {{
    receipt.key_delivery_mode = 'burner';
    try {{
      if (!window.SquishBurnerLinks?.create) throw new Error('The single-use link module is not ready');
      const burner = await window.SquishBurnerLinks.create(password, {{ttlHours: 24}});
      receipt.burner_url = burner.url;
      receipt.burner_expires_at = burner.expiresAt;
    }} catch (error) {{
      receipt.burner_error = error instanceof Error ? error.message : String(error);
    }}
  }}
  return receipt;
}};

if (window.onStaticToolsReady) {{
  window.onStaticToolsReady(STATIC_TOOLS);
}}
"""

out_path = os.path.join(os.path.dirname(__file__), 'static', 'client_tools.js')
with open(out_path, "w") as f:
    f.write(js_content)

worker_template_path = os.path.join(os.path.dirname(__file__), 'client_tools_worker.template.js')
with open(worker_template_path, 'r') as f:
    worker_content = f.read().replace('__TOOLS_PY_SOURCE_JSON__', json.dumps(tools_py_content))
worker_path = os.path.join(os.path.dirname(__file__), 'static', 'client_tools_worker.js')
with open(worker_path, 'w') as f:
    f.write(worker_content)

# Emit a clean, canonical tools.json (valid, pretty-printed) alongside the JS.
# Previously this file was produced by hand with stdout redirection, which
# leaked the PyMuPDF import banner into it and left it as invalid JSON.
registry_path = os.path.join(os.path.dirname(__file__), 'tools.json')
with open(registry_path, "w") as f:
    json.dump(registry_data, f, indent=2)
    f.write("\n")

print(f"Generated {out_path}, {worker_path}, and {registry_path} successfully!")
