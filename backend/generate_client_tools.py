import json
import os
import re

# Read original tools.json if we can, or just tools.py
import sys
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
import tools

# Filter out callables
tools_data = []
for t in tools.TOOLS:
    d = {k: v for k, v in vars(t).items() if not callable(v)}
    
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
        "compress":         "Ghostscript",   # gs
        "grayscale":        "Ghostscript",   # gs
        "pdf-to-pdfa":      "Ghostscript",   # gs
        "repair":           "qpdf",          # qpdf
        "office-to-pdf":    "LibreOffice",   # soffice
        "ocr":              "OCR engine",    # ocrmypdf + tesseract
        "pdf-to-word":      "pdf2docx",      # native deps, not wasm-safe
        # -- import a package with no usable wasm build --
        "md-to-pdf":        "WeasyPrint",    # needs native cairo/pango
        "sign-pdf":         "pyhanko",       # crypto stack, not Pyodide-installable
        "verify-signature": "pyhanko",
    }
    if d["key"] in unsupported:
        d["available"] = False
        d["needs"] = unsupported[d["key"]]
    else:
        d["available"] = True
        d["needs"] = ""
        
    tools_data.append(d)

tools_json = json.dumps(tools_data, separators=(',', ':'))

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

const TOOLS_PY_SOURCE = `{tools_py_content}`;

// Pyodide Runner Logic
let pyodide = null;
let pyodideReadyPromise = null;

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
        await micropip.install(['/vendor/pyodide/pymupdf-1.27.2.2-cp314-abi3-pyemscripten_2026_0_wasm32.whl', '/vendor/pyodide/markdown-3.10.3-py3-none-any.whl', 'pygments']);
      }} catch (e) {{
        console.error("Core engine packages failed to install", e);
        if(msg) msg.textContent = 'Engine failed to load — check your connection and reload.';
        throw e;
      }}
      // Optional packages: openpyxl -> pdf-to-excel, python-pptx -> pdf-to-powerpoint.
      // Best-effort: if they cannot be resolved we keep the core tools working;
      // the two dependent tools then fail per-run with a caught error rather
      // than taking down the whole engine.
      try {{
        await micropip.install(['openpyxl', 'python-pptx']);
      }} catch (e) {{
        console.warn("Optional export packages unavailable; pdf-to-excel / pdf-to-powerpoint may fail", e);
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
    
    # Run
    result = tool.fn(work_dir, input_paths, params)
    
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

window.runPyodideTool = async function(key, files, formData) {{
  await initPyodide();
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

if (window.onStaticToolsReady) {{
  window.onStaticToolsReady(STATIC_TOOLS);
}}
"""

out_path = os.path.join(os.path.dirname(__file__), 'static', 'client_tools.js')
with open(out_path, "w") as f:
    f.write(js_content)

print(f"Generated {{out_path}} successfully!")
