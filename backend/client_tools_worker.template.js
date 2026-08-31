import {loadPyodide} from '/vendor/pyodide/pyodide.mjs';

const TOOLS_PY_SOURCE = __TOOLS_PY_SOURCE_JSON__;
const CORE_WHEELS = [
  '/vendor/pyodide/pymupdf-1.27.2.2-cp314-abi3-pyemscripten_2026_0_wasm32.whl',
  '/vendor/pyodide/markdown-3.10.3-py3-none-any.whl',
  '/vendor/pyodide/pygments-2.20.0-py3-none-any.whl',
];
const OPTIONAL_PACKAGES = {'pdf-to-excel':'openpyxl', 'pdf-to-powerpoint':'python-pptx'};
let pyodidePromise;
const optionalPackages = new Map();
let queue = Promise.resolve();
const ocrRequests = new Map();
let ocrRequestSequence = 0;

function progress(id, message) {
  postMessage({type:'progress', id, message});
}

async function initialise(id) {
  if (!pyodidePromise) pyodidePromise = (async () => {
    progress(id, 'Loading the local PDF engine…');
    const pyodide = await loadPyodide({indexURL:'/vendor/pyodide/'});
    await pyodide.loadPackage('micropip');
    const micropip = pyodide.pyimport('micropip');
    // The PyMuPDF wasm wheel advertises pytest as a runtime dependency even
    // though Squish never uses it. Installing with dependencies disabled keeps
    // the offline bundle deterministic; Markdown and Pygments are explicitly
    // included in CORE_WHEELS above.
    await micropip.install(CORE_WHEELS, false, false);
    pyodide.FS.writeFile('/home/pyodide/tools.py', TOOLS_PY_SOURCE);
    pyodide.runPython(`
import json
import hashlib
import os
import sys
from pathlib import Path

os.environ["MAX_PAGES"] = "1000"
os.environ["MAX_RENDER_MP"] = "1200"
sys.path.insert(0, "/home/pyodide")
import fitz
import tools

def run_tool_wrapper(key, work_dir_json, files_json, params_json):
    work_dir = Path(json.loads(work_dir_json))
    work_dir.mkdir(parents=True, exist_ok=True)
    input_paths = [work_dir / name for name in json.loads(files_json)]
    params = json.loads(params_json)
    if key == "__dispatch-extract":
        out_path = tools._dispatch_extract_pages(
            work_dir, input_paths[0], params.get("pages", ""),
            params.get("pdf_open_password", ""))
        return {"name": out_path.name, "mime": "application/pdf"}
    tool = next(t for t in tools.TOOLS if t.key == key)
    fn_name = json.loads(params.pop("__static_fn_overrides", "{}" )).get(key)
    fn = getattr(tools, fn_name) if fn_name else tool.fn
    result = fn(work_dir, input_paths, params)
    return {"name": result.filename, "mime": result.media_type}

_ocr_doc = None
_ocr_force = False
_ocr_visual_hashes = []

def ocr_begin(path, force):
    global _ocr_doc, _ocr_force, _ocr_visual_hashes
    _ocr_doc = fitz.open(path)
    _ocr_force = bool(force)
    _ocr_visual_hashes = []
    if _ocr_doc.needs_pass:
        raise RuntimeError("Unlock the PDF before running OCR")
    return len(_ocr_doc)

def ocr_render(page_number):
    page = _ocr_doc[int(page_number)]
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False, colorspace=fitz.csRGB)
    _ocr_visual_hashes.append(hashlib.sha256(pix.samples).digest())
    if not _ocr_force and page.get_text("text").strip():
        return {"skip": True}
    return {"skip": False, "png": pix.tobytes("png"), "width": pix.width, "height": pix.height}

def ocr_apply(page_number, words_json, image_width, image_height):
    page = _ocr_doc[int(page_number)]
    sx = page.rect.width / float(image_width)
    sy = page.rect.height / float(image_height)
    for word in json.loads(words_json):
        text = str(word.get("text", "")).strip()
        if not text:
            continue
        left = float(word["left"]) * sx
        top = float(word["top"]) * sy
        height = max(1.0, float(word["height"]) * sy)
        baseline = min(page.rect.height, top + height * 0.88)
        page.insert_text(
            fitz.Point(left, baseline), text,
            fontsize=max(3.0, min(72.0, height * 0.82)),
            fontname="helv", render_mode=3, overlay=True)

def ocr_finish(output_path):
    global _ocr_doc, _ocr_visual_hashes
    output = Path(output_path)
    _ocr_doc.save(output, garbage=4, deflate=True, clean=True)
    _ocr_doc.close()
    _ocr_doc = None
    verified = fitz.open(output)
    try:
        text = "\n".join(page.get_text("text") for page in verified).strip()
        if not text:
            raise RuntimeError("OCR finished without producing a searchable text layer")
        for index, page in enumerate(verified):
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False, colorspace=fitz.csRGB)
            if hashlib.sha256(pix.samples).digest() != _ocr_visual_hashes[index]:
                raise RuntimeError(f"OCR changed the visible pixels on page {index + 1}")
    finally:
        verified.close()
        _ocr_visual_hashes = []
    return {"name": output.name, "mime": "application/pdf", "verified_text": text[:200]}
`);
    return pyodide;
  })();
  return pyodidePromise;
}

async function ensureOptionalPackage(pyodide, key, id) {
  const pkg = OPTIONAL_PACKAGES[key];
  if (!pkg) return;
  if (!optionalPackages.has(pkg)) optionalPackages.set(pkg, (async () => {
    progress(id, `Loading the optional ${pkg} package…`);
    await pyodide.pyimport('micropip').install(pkg);
  })());
  await optionalPackages.get(pkg);
}

function safeName(name, index) {
  return `${index + 1}-${String(name || 'input.pdf').replace(/[^A-Za-z0-9._-]/g, '_').slice(-180)}`;
}

async function ensureDiskSpace(files) {
  if (!navigator.storage?.estimate) return;
  const {usage = 0, quota = 0} = await navigator.storage.estimate();
  const inputBytes = files.reduce((sum, file) => sum + Number(file.size || 0), 0);
  const required = inputBytes * 2.25 + 64 * 1024 * 1024;
  if (quota && quota - usage < required) throw new Error(`Not enough private browser storage. Free about ${Math.ceil((required - (quota - usage)) / 1048576)} MB and try again.`);
}

async function stageFile(file, directory, name) {
  const handle = await directory.getFileHandle(name, {create:true});
  if (handle.createSyncAccessHandle) {
    const access = await handle.createSyncAccessHandle();
    try {
      access.truncate(0);
      const reader = file.stream().getReader();
      let offset = 0;
      while (true) {
        const {value, done} = await reader.read();
        if (done) break;
        access.write(value, {at:offset});
        offset += value.byteLength;
      }
      access.flush();
    } finally {
      access.close();
    }
  } else {
    const writable = await handle.createWritable();
    await file.stream().pipeTo(writable);
  }
}

async function createWorkspace(pyodide, id, files) {
  if (!navigator.storage?.getDirectory || !pyodide.mountNativeFS) return createMemoryWorkspace(pyodide, files);
  await ensureDiskSpace(files);
  navigator.storage.persist?.().catch(() => false);
  const root = await navigator.storage.getDirectory();
  const jobs = await root.getDirectoryHandle('squish-jobs', {create:true});
  const jobName = id.replace(/[^A-Za-z0-9_-]/g, '_');
  try { await jobs.removeEntry(jobName, {recursive:true}); } catch {}
  const directory = await jobs.getDirectoryHandle(jobName, {create:true});
  const names = files.map((file, index) => safeName(file.name, index));
  for (let i = 0; i < files.length; i++) await stageFile(files[i], directory, names[i]);
  const mountPoint = `/mnt/${jobName}`;
  pyodide.FS.mkdirTree(mountPoint);
  const mount = await pyodide.mountNativeFS(mountPoint, directory);
  return {
    kind:'opfs', directory, names, path:mountPoint,
    async output(name, mime) {
      await mount.syncfs();
      return new File([await (await directory.getFileHandle(name)).getFile()], name, {type:mime});
    },
    async cleanup() {
      try { pyodide.FS.unmount(mountPoint); } catch {}
      await jobs.removeEntry(jobName, {recursive:true});
    },
  };
}

async function createMemoryWorkspace(pyodide, files) {
  const path = `/tmp/squish-${crypto.randomUUID()}`;
  pyodide.FS.mkdirTree(path);
  const names = files.map((file, index) => safeName(file.name, index));
  for (let i = 0; i < files.length; i++) pyodide.FS.writeFile(`${path}/${names[i]}`, new Uint8Array(await files[i].arrayBuffer()));
  return {
    kind:'memory', names, path,
    async output(name, mime) { return new Blob([pyodide.FS.readFile(`${path}/${name}`)], {type:mime}); },
    async cleanup() {
      pyodide.runPython(`import shutil; shutil.rmtree(${JSON.stringify(path)}, ignore_errors=True)`);
    },
  };
}

function fromPython(proxy) {
  try { return proxy.toJs({dict_converter:Object.fromEntries}); }
  finally { proxy.destroy(); }
}

function recognisePage(image, lang, id) {
  const requestId = `${id}-ocr-${++ocrRequestSequence}`;
  return new Promise((resolve, reject) => {
    ocrRequests.set(requestId, {resolve, reject});
    postMessage({type:'ocr-page', id, requestId, lang, image});
  });
}

function parseTsv(tsv) {
  return String(tsv || '').split(/\r?\n/).slice(1).map(line => line.split('\t')).filter(cols => cols.length >= 12 && cols[0] === '5' && cols[11].trim()).map(cols => ({
    left:Number(cols[6]), top:Number(cols[7]), width:Number(cols[8]), height:Number(cols[9]), text:cols.slice(11).join('\t'),
  })).filter(word => [word.left, word.top, word.width, word.height].every(Number.isFinite));
}

async function runOcr(pyodide, workspace, params, id) {
  const lang = String(params.lang || 'eng');
  if (!['eng','fra','deu','spa','por','ita'].includes(lang)) throw new Error('That OCR language is not installed');
  const begin = pyodide.globals.get('ocr_begin');
  const render = pyodide.globals.get('ocr_render');
  const apply = pyodide.globals.get('ocr_apply');
  const finish = pyodide.globals.get('ocr_finish');
  const count = Number(begin(`${workspace.path}/${workspace.names[0]}`, params.force === '1' || params.force === true));
  try {
    for (let page = 0; page < count; page++) {
      progress(id, `OCR page ${page + 1} of ${count}…`);
      const rendered = fromPython(render(page));
      if (rendered.skip) continue;
      const image = new Blob([rendered.png], {type:'image/png'});
      const tsv = await recognisePage(image, lang, id);
      apply(page, JSON.stringify(parseTsv(tsv)), rendered.width, rendered.height);
    }
    const inputStem = workspace.names[0].replace(/\.pdf$/i, '');
    return fromPython(finish(`${workspace.path}/${inputStem}_ocr.pdf`));
  } finally {
    begin.destroy(); render.destroy(); apply.destroy(); finish.destroy();
  }
}

async function runJob(message) {
  const {id, key, files, params} = message;
  let workspace;
  try {
    const pyodide = await initialise(id);
    await ensureOptionalPackage(pyodide, key, id);
    progress(id, 'Staging files in private browser storage…');
    workspace = await createWorkspace(pyodide, id, files);
    let result;
    if (key === 'ocr') result = await runOcr(pyodide, workspace, params, id);
    else {
      progress(id, workspace.kind === 'opfs' ? 'Processing from private disk…' : 'Processing locally…');
      const wrapper = pyodide.globals.get('run_tool_wrapper');
      try { result = fromPython(wrapper(key, JSON.stringify(workspace.path), JSON.stringify(workspace.names), JSON.stringify(params))); }
      finally { wrapper.destroy(); }
    }
    const blob = await workspace.output(result.name, result.mime || 'application/octet-stream');
    const storage = workspace.kind;
    postMessage({type:'result', id, blob, name:result.name, storage, verifiedText:result.verified_text || ''});
    await workspace.cleanup();
    workspace = null;
    postMessage({type:'cleanup', id});
  } catch (error) {
    postMessage({type:'error', id, error:error instanceof Error ? error.message : String(error)});
  } finally {
    await workspace?.cleanup();
  }
}

self.onmessage = event => {
  if (event.data?.type === 'ocr-result') {
    const pending = ocrRequests.get(event.data.requestId);
    if (!pending) return;
    ocrRequests.delete(event.data.requestId);
    if (event.data.error) pending.reject(new Error(event.data.error));
    else pending.resolve(event.data.tsv || '');
    return;
  }
  if (event.data?.type !== 'run') return;
  queue = queue.then(() => runJob(event.data), () => runJob(event.data));
};
