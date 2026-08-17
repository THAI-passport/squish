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
    
    # Apply static mode availability
    unsupported = ["compress", "office-to-pdf", "ocr", "repair", "pdf-to-pdfa", "pdf-to-word"]
    if d["key"] in unsupported:
        d["available"] = False
        d["needs"] = "backend binary"
    else:
        d["available"] = True
        d["needs"] = ""
        
    tools_data.append(d)

tools_json = json.dumps(tools_data, separators=(',', ':'))

# Read tools.py content
with open(os.path.join(os.path.dirname(__file__), 'tools.py'), 'r') as f:
    tools_py_content = f.read()

# Escape backticks and dollars for JS template literal
tools_py_content = tools_py_content.replace('\\', '\\\\').replace('`', '\\`').replace('$', '\\$')

js_content = f"""
// Auto-generated client_tools.js for static WASM fallback
const STATIC_TOOLS = {tools_json};

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
      script.src = 'https://cdn.jsdelivr.net/pyodide/v314.0.5/full/pyodide.js';
      document.head.append(script);
      
      await new Promise((resolve) => script.onload = resolve);
      
      pyodide = await loadPyodide();
      
      if(msg) msg.textContent = 'Installing packages...';
      await pyodide.loadPackage('micropip');
      const micropip = pyodide.pyimport('micropip');
      
      try {{
        // Basic packages
        await micropip.install(['pymupdf', 'markdown', 'pygments']);
      }} catch (e) {{
        console.error("Failed to install basic Pyodide packages", e);
      }}
      
      // Write tools.py to virtual file system
      pyodide.FS.writeFile('/home/pyodide/tools.py', TOOLS_PY_SOURCE);
      
      // Add /home/pyodide to sys.path and import
      pyodide.runPython(`
import sys
import os
sys.path.insert(0, '/home/pyodide')

# Mock some things that might fail during import in WebAssembly
os.environ["MAX_PAGES"] = "100"
os.environ["MAX_MEGAPIXELS"] = "100"

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
