
// Auto-generated client_tools.js for static WASM fallback
const STATIC_TOOLS = [{"key":"merge","name":"Merge PDF","group":"Organize","blurb":"Combine several PDFs into one, in the order you arrange them.","accept":".pdf","multi":true,"min_files":2,"fields":[{"name":"output_name","kind":"text","label":"Output name","placeholder":"merged","help":"Auto-filled from your files; edit if you like. The .pdf extension is added automatically."}],"available":true,"needs":""},{"key":"split","name":"Split PDF","group":"Organize","blurb":"Pull out a range, or burst the file into separate documents.","accept":".pdf","multi":false,"min_files":1,"fields":[{"name":"mode","kind":"select","label":"Mode","options":[["ranges","Extract selected pages into one PDF"],["every","One PDF per page"],["chunks","Fixed-size groups"]],"default":"ranges"},{"name":"pages","kind":"text","label":"Pages","placeholder":"all, or 1-3,7,10-","help":"Blank means every page.","picker":"select"},{"name":"size","kind":"number","label":"Pages per group","default":2,"min":1,"max":500}],"available":true,"needs":""},{"key":"remove-pages","name":"Remove pages","group":"Organize","blurb":"Delete the pages you name and keep everything else.","accept":".pdf","multi":false,"min_files":1,"fields":[{"name":"pages","kind":"text","label":"Pages to remove","placeholder":"e.g. 2 or 4-6","help":"The pages to delete.","picker":"remove"}],"available":true,"needs":""},{"key":"organize","name":"Reorder pages","group":"Organize","blurb":"Rebuild the document in an explicit page order.","accept":".pdf","multi":false,"min_files":1,"fields":[{"name":"pages","kind":"text","label":"New order","placeholder":"3,1,2,4-","help":"Pages appear in exactly this order.","picker":"order"}],"available":true,"needs":""},{"key":"rotate","name":"Rotate PDF","group":"Organize","blurb":"Turn pages a quarter, half or three-quarter turn.","accept":".pdf","multi":false,"min_files":1,"fields":[{"name":"angle","kind":"select","label":"Rotation","options":[["90","90 clockwise"],["180","180"],["270","90 counter-clockwise"]],"default":"90"},{"name":"pages","kind":"text","label":"Pages","placeholder":"all, or 1-3,7,10-","help":"Blank means every page.","picker":"select"}],"available":true,"needs":""},{"key":"split-bookmarks","name":"Split at bookmarks","group":"Organize","blurb":"Break a long document into sections using its own outline.","accept":".pdf","multi":false,"min_files":1,"fields":[{"name":"level","kind":"select","label":"Split at","options":[["1","Top-level bookmarks only"],["2","Level 2 and above"],["3","Level 3 and above"]],"default":"1"}],"available":true,"needs":""},{"key":"n-up","name":"N-up / booklet","group":"Organize","blurb":"Print two or four pages per sheet to save paper.","accept":".pdf","multi":false,"min_files":1,"fields":[{"name":"per_sheet","kind":"select","label":"Pages per sheet","options":[["2","2 up"],["4","4 up"]],"default":"2"},{"name":"gap","kind":"number","label":"Gap (pt)","default":8,"min":0,"max":72},{"name":"pages","kind":"text","label":"Pages","placeholder":"all, or 1-3,7,10-","help":"Blank means every page.","picker":"select"}],"available":true,"needs":""},{"key":"compress","name":"Compress PDF","group":"Optimize","blurb":"Clean, deduplicate and deflate PDF objects without uploading the file.","accept":".pdf","multi":false,"min_files":1,"fields":[{"name":"level","kind":"select","label":"Compression","options":[["low","Less compression, best quality"],["recommended","Recommended"],["extreme","Extreme, smallest file"]],"default":"recommended"}],"available":true,"needs":""},{"key":"repair","name":"Repair PDF","group":"Optimize","blurb":"Best-effort recovery of damaged PDF structure, entirely in your browser.","accept":".pdf","multi":false,"min_files":1,"fields":null,"available":true,"needs":""},{"key":"grayscale","name":"Grayscale PDF","group":"Optimize","blurb":"Rebuild pages in grayscale for cheaper printing (browser mode rasterises pages).","accept":".pdf","multi":false,"min_files":1,"fields":null,"available":true,"needs":""},{"key":"ocr","name":"OCR PDF","group":"Optimize","blurb":"Recognise scans in a background WASM worker and add an invisible searchable text layer without uploading the PDF.","accept":".pdf","multi":false,"min_files":1,"fields":[{"name":"lang","kind":"select","label":"Language","options":[["eng","English"],["fra","French"],["deu","German"],["spa","Spanish"],["por","Portuguese"],["ita","Italian"]],"default":"eng"},{"name":"deskew","kind":"checkbox","label":"Straighten crooked scans"},{"name":"force","kind":"checkbox","label":"Re-OCR pages that already have text"}],"available":true,"needs":""},{"key":"jpg-to-pdf","name":"Image to PDF","group":"Convert to PDF","blurb":"Turn JPG, PNG or WEBP images into a PDF, one image per page.","accept":".jpg,.jpeg,.png,.webp,.bmp,.tif,.tiff","multi":true,"min_files":1,"fields":[{"name":"size","kind":"select","label":"Page size","options":[["fit","Fit to each image"],["a4","A4"],["letter","US Letter"]],"default":"fit"},{"name":"margin","kind":"number","label":"Margin (pt)","default":0,"min":0,"max":200}],"available":true,"needs":""},{"key":"office-to-pdf","name":"Office to PDF","group":"Convert to PDF","blurb":"Word, Excel and PowerPoint files rendered to PDF by LibreOffice.","accept":".csv,.doc,.docx,.odp,.ods,.odt,.ppt,.pptx,.rtf,.txt,.xls,.xlsx","multi":true,"min_files":1,"fields":null,"available":false,"needs":"LibreOffice"},{"key":"md-to-pdf","name":"Markdown to PDF","group":"Convert to PDF","blurb":"Render Markdown to PDF locally with browser-safe page layout.","accept":".md,.markdown,.mdown,.txt","multi":false,"min_files":0,"fields":[{"name":"md_text","kind":"textarea","label":"Markdown text","placeholder":"# Title\n\nPaste or type Markdown here. Used only when no file is uploaded."},{"name":"page_size","kind":"select","label":"Page size","options":[["a4","A4"],["letter","US Letter"]],"default":"a4"},{"name":"margin","kind":"number","label":"Margin (mm)","default":18,"min":0,"max":50},{"name":"title","kind":"text","label":"Document title","help":"Defaults to the file name."},{"name":"allow_remote","kind":"checkbox","label":"Allow remote images","help":"Off by default. Fetches https images referenced in the document; private/loopback addresses stay blocked."}],"available":true,"needs":""},{"key":"pdf-to-jpg","name":"PDF to image","group":"Convert from PDF","blurb":"Render each page as a JPG or PNG at the resolution you choose.","accept":".pdf","multi":false,"min_files":1,"fields":[{"name":"format","kind":"select","label":"Format","options":[["jpg","JPG"],["png","PNG"]],"default":"jpg"},{"name":"dpi","kind":"number","label":"Resolution (DPI)","default":150,"min":36,"max":600},{"name":"pages","kind":"text","label":"Pages","placeholder":"all, or 1-3,7,10-","help":"Blank means every page.","picker":"select"}],"available":true,"needs":""},{"key":"pdf-to-word","name":"PDF to Word","group":"Convert from PDF","blurb":"Rebuild paragraphs, tables and images as an editable .docx.","accept":".pdf","multi":false,"min_files":1,"fields":null,"available":false,"needs":"pdf2docx"},{"key":"pdf-to-excel","name":"PDF to Excel","group":"Convert from PDF","blurb":"Extract detected tables into a worksheet each.","accept":".pdf","multi":false,"min_files":1,"fields":[{"name":"pages","kind":"text","label":"Pages","placeholder":"all, or 1-3,7,10-","help":"Blank means every page.","picker":"select"}],"available":true,"needs":""},{"key":"pdf-to-powerpoint","name":"PDF to PowerPoint","group":"Convert from PDF","blurb":"One slide per page, rendered full bleed.","accept":".pdf","multi":false,"min_files":1,"fields":[{"name":"dpi","kind":"number","label":"Resolution (DPI)","default":150,"min":72,"max":300},{"name":"pages","kind":"text","label":"Pages","placeholder":"all, or 1-3,7,10-","help":"Blank means every page.","picker":"select"}],"available":true,"needs":""},{"key":"pdf-to-pdfa","name":"PDF to PDF/A","group":"Convert from PDF","blurb":"Convert to the PDF/A-2b archival standard.","accept":".pdf","multi":false,"min_files":1,"fields":null,"available":false,"needs":"Ghostscript"},{"key":"pdf-to-markdown","name":"PDF to Markdown","group":"Convert from PDF","blurb":"Extract the text layer as Markdown, page by page.","accept":".pdf","multi":false,"min_files":1,"fields":[{"name":"pages","kind":"text","label":"Pages","placeholder":"all, or 1-3,7,10-","help":"Blank means every page.","picker":"select"}],"available":true,"needs":""},{"key":"pdf-to-text","name":"PDF to Text","group":"Convert from PDF","blurb":"Extract the raw text layer as a .txt file.","accept":".pdf","multi":false,"min_files":1,"fields":[{"name":"pages","kind":"text","label":"Pages","placeholder":"all, or 1-3,7,10-","help":"Blank means every page.","picker":"select"}],"available":true,"needs":""},{"key":"extract-images","name":"Extract images","group":"Convert from PDF","blurb":"Recover embedded images at their original resolution.","accept":".pdf","multi":false,"min_files":1,"fields":[{"name":"min_size","kind":"number","label":"Ignore images smaller than (px)","default":64,"min":0,"max":4000,"help":"Filters out rules, bullets and spacer pixels."},{"name":"pages","kind":"text","label":"Pages","placeholder":"all, or 1-3,7,10-","help":"Blank means every page.","picker":"select"}],"available":true,"needs":""},{"key":"extract-attachments","name":"Extract attachments","group":"Convert from PDF","blurb":"Pull out files embedded inside the PDF.","accept":".pdf","multi":false,"min_files":1,"fields":null,"available":true,"needs":""},{"key":"extract-fonts","name":"Extract fonts","group":"Convert from PDF","blurb":"Recover embedded font files.","accept":".pdf","multi":false,"min_files":1,"fields":[{"name":"pages","kind":"text","label":"Pages","placeholder":"all, or 1-3,7,10-","help":"Blank means every page.","picker":"select"}],"available":true,"needs":""},{"key":"watermark","name":"Add watermark","group":"Edit","blurb":"Stamp text across the page, once or tiled.","accept":".pdf","multi":false,"min_files":1,"fields":[{"name":"text","kind":"text","label":"Watermark text","placeholder":"CONFIDENTIAL","required":true},{"name":"mode","kind":"select","label":"Layout","options":[["single","Once per page"],["tile","Tiled"]],"default":"single"},{"name":"position","kind":"select","label":"Position","options":[["top-left","top left"],["top","top"],["top-right","top right"],["left","left"],["center","center"],["right","right"],["bottom-left","bottom left"],["bottom","bottom"],["bottom-right","bottom right"]],"default":"center"},{"name":"size","kind":"number","label":"Font size","default":42,"min":6,"max":200},{"name":"opacity","kind":"number","label":"Opacity","default":0.25,"min":0.05,"max":1,"step":0.05},{"name":"angle","kind":"number","label":"Angle","default":45,"min":-180,"max":180},{"name":"color","kind":"color","label":"Colour","default":"#7c5cff"},{"name":"pages","kind":"text","label":"Pages","placeholder":"all, or 1-3,7,10-","help":"Blank means every page.","picker":"select"}],"available":true,"needs":""},{"key":"page-numbers","name":"Add page numbers","group":"Edit","blurb":"Number the pages, with your own format and placement.","accept":".pdf","multi":false,"min_files":1,"fields":[{"name":"format","kind":"text","label":"Format","default":"{n}","help":"{n} is the number, {total} the count. e.g. Page {n} of {total}"},{"name":"position","kind":"select","label":"Position","options":[["top-left","top left"],["top","top"],["top-right","top right"],["left","left"],["center","center"],["right","right"],["bottom-left","bottom left"],["bottom","bottom"],["bottom-right","bottom right"]],"default":"bottom"},{"name":"start","kind":"number","label":"Start at","default":1,"min":0,"max":99999},{"name":"size","kind":"number","label":"Font size","default":11,"min":6,"max":72},{"name":"color","kind":"color","label":"Colour","default":"#000000"},{"name":"pages","kind":"text","label":"Pages","placeholder":"all, or 1-3,7,10-","help":"Blank means every page.","picker":"select"}],"available":true,"needs":""},{"key":"crop","name":"Crop PDF","group":"Edit","blurb":"Trim margins off every page, measured in points.","accept":".pdf","multi":false,"min_files":1,"fields":[{"name":"top","kind":"number","label":"Top (pt)","default":0,"min":0,"max":1000},{"name":"bottom","kind":"number","label":"Bottom (pt)","default":0,"min":0,"max":1000},{"name":"left","kind":"number","label":"Left (pt)","default":0,"min":0,"max":1000},{"name":"right","kind":"number","label":"Right (pt)","default":0,"min":0,"max":1000},{"name":"pages","kind":"text","label":"Pages","placeholder":"all, or 1-3,7,10-","help":"Blank means every page.","picker":"select"}],"available":true,"needs":""},{"key":"header-footer","name":"Header / footer","group":"Edit","blurb":"Running text at the top or bottom, with date and filename tokens.","accept":".pdf","multi":false,"min_files":1,"fields":[{"name":"text","kind":"text","label":"Text","required":true,"placeholder":"Draft \u2014 {date} \u2014 page {n} of {total}","help":"Tokens: {n} {total} {date} {filename}"},{"name":"position","kind":"select","label":"Position","options":[["top-left","top left"],["top","top"],["top-right","top right"],["left","left"],["center","center"],["right","right"],["bottom-left","bottom left"],["bottom","bottom"],["bottom-right","bottom right"]],"default":"top"},{"name":"size","kind":"number","label":"Font size","default":10,"min":6,"max":72},{"name":"color","kind":"color","label":"Colour","default":"#555555"},{"name":"pages","kind":"text","label":"Pages","placeholder":"all, or 1-3,7,10-","help":"Blank means every page.","picker":"select"}],"available":true,"needs":""},{"key":"flatten","name":"Flatten PDF","group":"Edit","blurb":"Bake annotations and form fields in so they can no longer be edited.","accept":".pdf","multi":false,"min_files":1,"fields":null,"available":true,"needs":""},{"key":"metadata","name":"Edit metadata","group":"Edit","blurb":"Change the title and author, or strip identifying metadata entirely.","accept":".pdf","multi":false,"min_files":1,"fields":[{"name":"strip","kind":"checkbox","label":"Strip everything (ignores the fields below)"},{"name":"title","kind":"text","label":"Title"},{"name":"author","kind":"text","label":"Author"},{"name":"subject","kind":"text","label":"Subject"},{"name":"keywords","kind":"text","label":"Keywords"}],"available":true,"needs":""},{"key":"rasterise","name":"Rasterise PDF","group":"Security","blurb":"Rebuild every page as a flat image, destroying scripts and form fields.","accept":".pdf","multi":false,"min_files":1,"fields":[{"name":"dpi","kind":"number","label":"Resolution (DPI)","default":150,"min":72,"max":400},{"name":"pages","kind":"text","label":"Pages","placeholder":"all, or 1-3,7,10-","help":"Blank means every page.","picker":"select"}],"available":true,"needs":""},{"key":"protect","name":"Protect PDF","group":"Security","blurb":"Encrypt with AES-256 and set what readers are allowed to do.","accept":".pdf","multi":false,"min_files":1,"fields":[{"name":"password_new","kind":"password","label":"New password","required":true},{"name":"allow_copy","kind":"checkbox","label":"Allow copying text"},{"name":"allow_modify","kind":"checkbox","label":"Allow editing and annotation"}],"available":true,"needs":""},{"key":"email-secure","name":"Secure Email Dispatch","group":"Security","blurb":"Encrypt and dispatch PDFs with optional bursting, signing, a removable leak marker, and out-of-band key delivery.","accept":".pdf,.p12,.pfx","multi":true,"min_files":1,"fields":[{"name":"recipient_email","kind":"text","label":"Recipient email address","required":true,"placeholder":"recipient@example.com","help":"Destination inbox for the secure document and password."},{"name":"email_subject","kind":"text","label":"Email subject","placeholder":"Confidential Document","help":"Optional subject line."},{"name":"password_mode","kind":"select","label":"Password mode","options":[["random","Auto-generate strong 16-character password"],["manual","Specify custom password"]],"default":"random"},{"name":"custom_password","kind":"password","label":"Custom password","placeholder":"Enter password (if manual mode selected)","help":"Only used if manual password mode is chosen."},{"name":"recipient_watermark","kind":"checkbox","label":"Stamp recipient leak watermark"}],"available":false,"needs":"Cloudflare email worker"},{"key":"unlock","name":"Unlock PDF","group":"Security","blurb":"Strip encryption from a PDF whose password you know.","accept":".pdf","multi":false,"min_files":1,"fields":null,"available":true,"needs":""},{"key":"redact","name":"Redact PDF","group":"Security","blurb":"Permanently erase matching text -- not a black box drawn on top.","accept":".pdf","multi":false,"min_files":1,"fields":[{"name":"terms","kind":"textarea","label":"Terms to redact","required":true,"placeholder":"one term per line"},{"name":"color","kind":"color","label":"Box colour","default":"#000000"},{"name":"pages","kind":"text","label":"Pages","placeholder":"all, or 1-3,7,10-","help":"Blank means every page.","picker":"select"}],"available":true,"needs":""},{"key":"auto-redact","name":"Auto-redact","group":"Security","blurb":"Best-effort pattern redaction for text layers. WARNING: scans, split spans, ligatures, and layout can cause misses; always verify the output.","accept":".pdf,.txt,.csv","multi":true,"min_files":1,"fields":[{"name":"redact_email","kind":"checkbox","label":"Redact Emails"},{"name":"redact_ssn","kind":"checkbox","label":"Redact SSNs"},{"name":"redact_phone","kind":"checkbox","label":"Redact Phone Numbers"},{"name":"redact_cc","kind":"checkbox","label":"Redact Credit Cards (Luhn validated)"},{"name":"redact_iban","kind":"checkbox","label":"Redact IBAN Bank Accounts (ISO Mod-97)"},{"name":"terms","kind":"textarea","label":"Custom dictionary terms","placeholder":"one term per line"},{"name":"color","kind":"color","label":"Box colour","default":"#000000"},{"name":"pages","kind":"text","label":"Pages","placeholder":"all, or 1-3,7,10-","help":"Blank means every page.","picker":"select"}],"available":true,"needs":""},{"key":"sign-pdf","name":"Sign PDF","group":"Security","blurb":"Digitally sign a PDF using your own .p12 or .pfx certificate.","accept":".pdf,.p12,.pfx","multi":true,"min_files":2,"fields":[{"name":"password_cert","kind":"password","label":"Certificate password","required":true}],"available":false,"needs":"pyhanko"},{"key":"verify-signature","name":"Verify Signature","group":"Security","blurb":"Check the validity of digital signatures on a PDF.","accept":".pdf","multi":false,"min_files":1,"fields":null,"available":false,"needs":"pyhanko"},{"key":"compare","name":"Compare PDFs","group":"Security","blurb":"Line-by-line diff of two documents' text.","accept":".pdf","multi":true,"min_files":2,"fields":null,"available":true,"needs":""}];
const STATIC_VERSION = "2.0.2-squish";
window.STATIC_VERSION = STATIC_VERSION;
const STATIC_FN_OVERRIDES = {"compress":"compress_wasm","repair":"repair_wasm","grayscale":"grayscale_wasm","md-to-pdf":"md_to_pdf_wasm"};

// All Python/WASM work runs off the UI thread. Files are structured-cloned as
// Blob handles, then staged to OPFS inside the worker without a whole-file JS
// ArrayBuffer on the main thread.
let processingWorker = null;
let processingSequence = 0;
const processingJobs = new Map();
let tesseractModule = null;
let tesseractWorker = null;
let tesseractLanguage = '';

async function recogniseOcrPage(message) {
  try {
    if (!tesseractModule) tesseractModule = await import('/vendor/tesseract/tesseract.esm.min.js');
    if (!tesseractWorker || tesseractLanguage !== message.lang) {
      if (tesseractWorker) await tesseractWorker.terminate();
      const status = document.getElementById('fileStatus');
      if (status) status.textContent = `Loading ${message.lang} OCR model (cached after first use)…`;
      const tesseract = tesseractModule.createWorker ? tesseractModule : tesseractModule.default;
      tesseractWorker = await tesseract.createWorker(message.lang, 1, {
        workerPath:'/vendor/tesseract/worker.min.js',
        corePath:'/vendor/tesseract/core',
        langPath:'/vendor/tesseract/lang',
        cachePath:'squish-ocr-v1',
        workerBlobURL:false,
        errorHandler:error => console.error('OCR worker error', error),
        logger: update => {
          if (status && update.status && Number.isFinite(update.progress)) status.textContent = `${update.status} · ${Math.round(update.progress * 100)}%`;
        },
      });
      tesseractLanguage = message.lang;
    }
    const result = await tesseractWorker.recognize(message.image, {}, {text:false, blocks:false, tsv:true});
    processingWorker?.postMessage({type:'ocr-result', requestId:message.requestId, tsv:result.data.tsv || ''});
  } catch (error) {
    processingWorker?.postMessage({type:'ocr-result', requestId:message.requestId, error:error instanceof Error ? error.message : String(error)});
  }
}

function ensureProcessingWorker() {
  if (processingWorker) return processingWorker;
  processingWorker = new Worker('/client_tools_worker.js?v=2.0.2-squish', {type:'module'});
  processingWorker.onmessage = event => {
    const message = event.data || {};
    if (message.type === 'ocr-page') { recogniseOcrPage(message); return; }
    const job = processingJobs.get(message.id);
    if (!job) return;
    if (message.type === 'progress') {
      const status = document.getElementById('fileStatus');
      if (status) status.textContent = message.message || 'Processing locally…';
      return;
    }
    if (message.type === 'result') {
      job.result = {
        blob:message.blob, name:message.name, storage:message.storage,
        verifiedText:message.verifiedText || ''
      };
      return;
    }
    processingJobs.delete(message.id);
    if (message.type === 'cleanup') job.resolve({...job.result, cleaned:true});
    else job.reject(new Error(message.error || 'Local processing failed'));
  };
  processingWorker.onerror = error => {
    for (const job of processingJobs.values()) job.reject(new Error(error.message || 'Browser worker stopped'));
    processingJobs.clear();
    processingWorker?.terminate();
    processingWorker = null;
  };
  return processingWorker;
}

window.runPyodideTool = function(key, files, formData) {
  const id = `squish-${Date.now()}-${++processingSequence}`;
  const params = {};
  for (const [name, value] of formData.entries()) if (name !== 'files') params[name] = value;
  params.__static_fn_overrides = JSON.stringify(STATIC_FN_OVERRIDES);
  return new Promise((resolve, reject) => {
    processingJobs.set(id, {resolve, reject});
    ensureProcessingWorker().postMessage({type:'run', id, key, files:Array.from(files), params});
  });
};

window.cancelLocalProcessing = function() {
  if (!processingWorker) return;
  processingWorker.terminate();
  processingWorker = null;
  for (const job of processingJobs.values()) job.reject(new Error('Processing cancelled'));
  processingJobs.clear();
  if (tesseractWorker) { tesseractWorker.terminate(); tesseractWorker = null; tesseractLanguage = ''; }
};

function staticRandomSecret(bytes = 16) {
  const raw = new Uint8Array(bytes);
  crypto.getRandomValues(raw);
  return Array.from(raw, b => b.toString(16).padStart(2, '0')).join('');
}

async function blobToBase64(blob) {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let binary = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

window.runStaticSecureEmail = async function(files, formData) {
  let pdfs = files.filter(f => /\.pdf$/i.test(f.name));
  if (!pdfs.length) throw new Error('Secure Email Dispatch needs at least one PDF file.');
  if (pdfs.length > 10) throw new Error('A maximum of 10 PDF attachments is allowed.');
  const password = String(formData.get('custom_password') || '').trim();
  if (password.length < 4) throw new Error('Password must be at least 4 characters.');

  const dispatchPages = String(formData.get('dispatch_pages') || '').trim();
  if (dispatchPages) {
    if (pdfs.length !== 1) throw new Error('Split & Dispatch accepts exactly one master PDF.');
    const splitData = new FormData();
    splitData.append('pages', dispatchPages);
    const selected = await window.runPyodideTool('__dispatch-extract', [pdfs[0]], splitData);
    pdfs = [new File([selected.blob], selected.name, {type: 'application/pdf'})];
  }

  const protectedResults = [];
  for (const pdf of pdfs) {
    const protectData = new FormData();
    protectData.append('password_new', password);
    protectData.append('owner_password', staticRandomSecret());
    protectedResults.push(await window.runPyodideTool('protect', [pdf], protectData));
  }

  let smtp = {};
  const saved = formData.get('smtp_profile_json');
  if (saved) {
    try { smtp = JSON.parse(saved); } catch { throw new Error('Saved SMTP profile is invalid.'); }
  } else {
    smtp = {
      server: String(formData.get('mail_server') || ''),
      port: Number(formData.get('mail_port') || 587),
      username: String(formData.get('mail_username') || ''),
      password: String(formData.get('mail_password') || ''),
      from_name: String(formData.get('mail_from_name') || ''),
      security: String(formData.get('mail_security') || 'starttls')
    };
  }
  if (!smtp.server || !smtp.username || !smtp.password) {
    throw new Error('SMTP server, sender email, and SMTP password are required.');
  }

  const htmlFile = files.find(f => /\.(html?|txt)$/i.test(f.name));
  const customHtml = String(formData.get('email_body_html') || '');
  if (customHtml.replace(/\s/g, '').toLowerCase().includes('{{password}}')) {
    throw new Error('Email #1 templates cannot contain {password}.');
  }
  let resolvedHtml = customHtml;
  if (!resolvedHtml && htmlFile) {
    try {
      resolvedHtml = await htmlFile.text();
    } catch(e) {
      resolvedHtml = await new Promise((resolve) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => resolve('');
        reader.readAsText(htmlFile);
      });
    }
  }
  const requestedKeyDeliveryMode = String(formData.get('key_delivery_mode') || 'email');
  const payload = {
    smtp,
    recipient: String(formData.get('recipient_email') || ''),
    subject: String(formData.get('email_subject') || ''),
    attachments: await Promise.all(protectedResults.map(async result => ({
      base64: await blobToBase64(result.blob), filename: result.name
    }))),
    htmlBody: resolvedHtml,
    plainTextOnly: formData.get('email_plain_text_only') === '1',
    password,
    delaySeconds: Number(formData.get('delay_seconds') || 0.1),
    email2Subject: String(formData.get('email2_subject') || ''),
    email2Body: String(formData.get('email2_body') || ''),
    threadEmails: formData.get('thread_emails') === '1' || formData.get('thread_emails') === 'true',
    // The link secret must never be sent to the SMTP Worker. Burner mode sends
    // the encrypted PDF first, then creates the zero-knowledge link locally.
    keyDeliveryMode: requestedKeyDeliveryMode === 'burner' ? 'oob' : requestedKeyDeliveryMode
  };

  const response = await fetch('/api/t/email-secure', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  let receipt;
  try { receipt = await response.json(); } catch { throw new Error(`Email worker returned HTTP ${response.status}.`); }
  if (!response.ok && receipt.status !== 'partial_failure') {
    throw new Error(receipt.error || `Email worker returned HTTP ${response.status}.`);
  }
  if (requestedKeyDeliveryMode === 'burner' && receipt.status !== 'partial_failure') {
    receipt.key_delivery_mode = 'burner';
    try {
      if (!window.SquishBurnerLinks?.create) throw new Error('The single-use link module is not ready');
      const burner = await window.SquishBurnerLinks.create(password, {ttlHours: 24});
      receipt.burner_url = burner.url;
      receipt.burner_expires_at = burner.expiresAt;
    } catch (error) {
      receipt.burner_error = error instanceof Error ? error.message : String(error);
    }
  }
  return receipt;
};

if (window.onStaticToolsReady) {
  window.onStaticToolsReady(STATIC_TOOLS);
}
