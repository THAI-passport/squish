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
    "ocr":              "OCR engine",    # ocrmypdf + tesseract
    "pdf-to-word":      "pdf2docx",      # native deps, not wasm-safe
    # -- import a package with no usable wasm build --
    "sign-pdf":         "pyhanko",       # crypto stack, not Pyodide-installable
    "verify-signature": "pyhanko",
    # Enabled dynamically only when the Cloudflare Pages Worker answers its
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

static_blurb_overrides = {
    "compress": "Clean, deduplicate and deflate PDF objects without uploading the file.",
    "repair": "Best-effort recovery of damaged PDF structure, entirely in your browser.",
    "grayscale": "Rebuild pages in grayscale for cheaper printing (browser mode rasterises pages).",
    "md-to-pdf": "Render Markdown to PDF locally with browser-safe page layout.",
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

# Escape backticks and dollars for JS template literal
tools_py_content = tools_py_content.replace('\\', '\\\\').replace('`', '\\`').replace('$', '\\$')

js_content = f"""
// Auto-generated client_tools.js for static WASM fallback
const STATIC_TOOLS = {tools_json};
const STATIC_VERSION = "{app_version}";
window.STATIC_VERSION = STATIC_VERSION;
const STATIC_FN_OVERRIDES = {json.dumps(static_fn_overrides, separators=(',', ':'))};

const TOOLS_PY_SOURCE = `{tools_py_content}`;

// Pyodide Runner Logic
let pyodide = null;
let pyodideReadyPromise = null;
const optionalPackagePromises = {{}};

async function initPyodide() {{
  if (!pyodideReadyPromise) {{
    pyodideReadyPromise = (async () => {{
      const msg = document.getElementById('fileStatus');
      if(msg) msg.textContent = 'Loading Pyodide engine... (~20MB)';

      const script = document.createElement('script');
      script.src = '/vendor/pyodide/pyodide.js';
      document.head.append(script);

      await new Promise((resolve) => script.onload = resolve);

      pyodide = await loadPyodide();

      if(msg) msg.textContent = 'Installing packages...';
      await pyodide.loadPackage('micropip');
      const micropip = pyodide.pyimport('micropip');

      // Core engine — every PyMuPDF-backed tool needs this. If it fails, the
      // whole static mode is dead, so surface it and stop.
      try {{
        await micropip.install(['/vendor/pyodide/pymupdf-1.27.2.2-cp314-abi3-pyemscripten_2026_0_wasm32.whl', '/vendor/pyodide/markdown-3.10.3-py3-none-any.whl', '/vendor/pyodide/pygments-2.20.0-py3-none-any.whl']);
      }} catch (e) {{
        console.error("Core engine packages failed to install", e);
        if(msg) msg.textContent = 'Engine failed to load — check your connection and reload.';
        throw e;
      }}
      // Write tools.py to virtual file system
      pyodide.FS.writeFile('/home/pyodide/tools.py', TOOLS_PY_SOURCE);

      // Add /home/pyodide to sys.path and import
      pyodide.runPython(`
import sys
import os
sys.path.insert(0, '/home/pyodide')

# Constrain resource budgets for the browser BEFORE importing tools, since
# tools.py reads these into module-level constants at import time.
# NOTE: the names must match tools.py exactly — MAX_PAGES and MAX_RENDER_MP.
# (An earlier version set MAX_MEGAPIXELS, which tools.py never reads, so the
# render budget silently stayed at the 4000 MP server default and could OOM
# the tab.)
os.environ["MAX_PAGES"] = "100"
os.environ["MAX_RENDER_MP"] = "300"

import tools
from pathlib import Path
import json

def run_tool_wrapper(key, files_json, params_json):
    work_dir = Path("/tmp/squish_work")
    work_dir.mkdir(parents=True, exist_ok=True)

    files = json.loads(files_json)
    params = json.loads(params_json)

    input_paths = []
    for f in files:
        p = work_dir / f
        input_paths.append(p)

    # Find tool
    tool = next(t for t in tools.TOOLS if t.key == key)
    fn_name = json.loads(params.pop("__static_fn_overrides", "{{}}" )).get(key)
    fn = getattr(tools, fn_name) if fn_name else tool.fn

    # Run
    result = fn(work_dir, input_paths, params)

    # Read output
    out_path = result.path
    with open(out_path, 'rb') as f:
        data = f.read()

    return {{
        'name': result.filename,
        'mime': result.media_type,
        'data': data
    }}
`);

      if(msg) msg.textContent = 'Ready.';
    }})();
  }}
  return pyodideReadyPromise;
}}

async function ensureOptionalPackage(key) {{
  const packages = {{'pdf-to-excel': 'openpyxl', 'pdf-to-powerpoint': 'python-pptx'}};
  const pkg = packages[key];
  if (!pkg) return;
  if (!optionalPackagePromises[key]) {{
    optionalPackagePromises[key] = (async () => {{
      const msg = document.getElementById('fileStatus');
      if(msg) msg.textContent = `Loading optional ${{pkg}} package...`;
      const micropip = pyodide.pyimport('micropip');
      await micropip.install(pkg);
    }})();
  }}
  return optionalPackagePromises[key];
}}

window.runPyodideTool = async function(key, files, formData) {{
  await initPyodide();
  await ensureOptionalPackage(key);
  const msg = document.getElementById('fileStatus');
  if(msg) msg.textContent = 'Processing locally...';

  try {{
      // Write input files to virtual FS
      pyodide.FS.mkdir('/tmp/squish_work');
  }} catch(e) {{}}

  const fileNames = [];
  for(let i=0; i<files.length; i++) {{
    const arrayBuffer = await files[i].arrayBuffer();
    const safeName = files[i].name.replace(/[^a-zA-Z0-9.-]/g, '_');
    pyodide.FS.writeFile('/tmp/squish_work/' + safeName, new Uint8Array(arrayBuffer));
    fileNames.push(safeName);
  }}

  // Extract parameters from formData
  const params = {{}};
  for (let [k, v] of formData.entries()) {{
    if (k !== 'files') {{
      params[k] = v;
    }}
  }}
  params.__static_fn_overrides = JSON.stringify(STATIC_FN_OVERRIDES);

  // Convert dicts to JSON for safe passing to Python
  const filesJson = JSON.stringify(fileNames);
  const paramsJson = JSON.stringify(params);

  try {{
    // Call Python wrapper
    const pyWrapper = pyodide.globals.get('run_tool_wrapper');
    const pyResult = pyWrapper(key, filesJson, paramsJson);
    const jsResult = pyResult.toJs({{dict_converter: Object.fromEntries}});
    pyResult.destroy();

    // Convert back to JS Blob
    const uint8View = jsResult.data;
    const blob = new Blob([uint8View], {{type: jsResult.mime || 'application/pdf'}});
    const name = jsResult.name;

    return {{ blob: blob, name: name }};
  }} catch (e) {{
    console.error(e);
    throw new Error("Local processing failed: " + e.message);
  }} finally {{
    // Cleanup /tmp/squish_work/
    // We try to clean up, if it fails it's just MEMFS memory leak.
    try {{
      pyodide.runPython(`
import shutil
shutil.rmtree('/tmp/squish_work', ignore_errors=True)
`);
    }} catch(e) {{}}
  }}
}};

function staticRandomSecret(bytes = 24) {{
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
  const pdf = files.find(f => /\\.pdf$/i.test(f.name));
  if (!pdf) throw new Error('Secure Email Dispatch needs a PDF file.');
  const password = String(formData.get('custom_password') || '').trim();
  if (password.length < 4) throw new Error('Password must be at least 4 characters.');

  const protectData = new FormData();
  protectData.append('password_new', password);
  // Never reuse the emailed user password as the owner password: doing so
  // silently grants full PDF permissions to the recipient.
  protectData.append('owner_password', staticRandomSecret());
  const protectedResult = await window.runPyodideTool('protect', [pdf], protectData);

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
  const payload = {{
    smtp,
    recipient: String(formData.get('recipient_email') || ''),
    subject: String(formData.get('email_subject') || ''),
    pdfBase64: await blobToBase64(protectedResult.blob),
    pdfFilename: protectedResult.name,
    htmlBody: customHtml || (htmlFile ? await htmlFile.text() : ''),
    password,
    delaySeconds: Number(formData.get('delay_seconds') || 2.5),
    email2Subject: String(formData.get('email2_subject') || ''),
    email2Body: String(formData.get('email2_body') || '')
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
  return receipt;
}};

if (window.onStaticToolsReady) {{
  window.onStaticToolsReady(STATIC_TOOLS);
}}
"""

out_path = os.path.join(os.path.dirname(__file__), 'static', 'client_tools.js')
with open(out_path, "w") as f:
    f.write(js_content)

# Emit a clean, canonical tools.json (valid, pretty-printed) alongside the JS.
# Previously this file was produced by hand with stdout redirection, which
# leaked the PyMuPDF import banner into it and left it as invalid JSON.
registry_path = os.path.join(os.path.dirname(__file__), 'tools.json')
with open(registry_path, "w") as f:
    json.dump(registry_data, f, indent=2)
    f.write("\n")

print(f"Generated {out_path} and {registry_path} successfully!")
