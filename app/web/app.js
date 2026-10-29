"use strict";
const $ = id => document.getElementById(id);
const names = {as:"Assamese",bn:"Bengali",brx:"Bodo",doi:"Dogri",en:"English",gu:"Gujarati",hi:"Hindi",kn:"Kannada",kok:"Konkani",ks:"Kashmiri (Arabic)","ks-Deva":"Kashmiri (Devanagari)",mai:"Maithili",ml:"Malayalam",mni:"Manipuri (Meitei)","mni-Beng":"Manipuri (Bengali)",mr:"Marathi",ne:"Nepali",or:"Odia",pa:"Punjabi",sa:"Sanskrit",sat:"Santali",sd:"Sindhi (Devanagari)","sd-Arab":"Sindhi (Arabic)",ta:"Tamil",te:"Telugu",ur:"Urdu"};
const nativeNames = {as:"অসমীয়া",bn:"বাংলা",brx:"बड़ो",doi:"डोगरी",en:"English",gu:"ગુજરાતી",hi:"हिन्दी",kn:"ಕನ್ನಡ",kok:"कोंकणी",ks:"کٲشُر", "ks-Deva":"कॉशुर",mai:"मैथिली",ml:"മലയാളം",mni:"ꯃꯤꯇꯩꯂꯣꯟ", "mni-Beng":"মৈতৈলোন্",mr:"मराठी",ne:"नेपाली",or:"ଓଡ଼ିଆ",pa:"ਪੰਜਾਬੀ",sa:"संस्कृतम्",sat:"ᱥᱟᱱᱛᱟᱲᱤ",sd:"सिन्धी", "sd-Arab":"سنڌي",ta:"தமிழ்",te:"తెలుగు",ur:"اردو"};
const storageKey = "translation-studio-active-job";
let limits = null, busy = false, active = null;
let finishedDocument = null, previewJobId = null, previewRequest = null;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
try { active = JSON.parse(sessionStorage.getItem(storageKey)); } catch { /* Storage may be unavailable. */ }
if (!active || typeof active.documentId !== "string" || !["inspect","start","translate","result","preview","render"].includes(active.phase) || typeof active.language !== "string") active = null;
function saveActive() {
  try { if (active) sessionStorage.setItem(storageKey, JSON.stringify(active)); else sessionStorage.removeItem(storageKey); } catch { /* The current job still works without storage. */ }
}
function appError(code, message, recoverable = false) { return Object.assign(new Error(message), {code, recoverable}); }
async function request(url, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.body instanceof FormData ? 120000 : 30000);
  try {
    const response = await fetch(url, {...options, signal:controller.signal});
    let body;
    try { body = await response.json(); }
    catch { throw appError("invalid_server_response", "The server returned an unreadable response.", true); }
    if (!response.ok) throw appError(body.error?.code || "server_error", body.error?.message || `Request failed (${response.status}).`, response.status >= 500 && !body.error?.code);
    return body;
  } catch (error) {
    if (error.name === "AbortError" || error instanceof TypeError) throw appError("connection_lost", "The connection was interrupted. Your job may still be running on the server.", true);
    throw error;
  } finally { clearTimeout(timeout); }
}
function setBusy(value) {
  busy = value;
  const locked = value || Boolean(active);
  $("pdf-file").disabled = locked;
  $("language").disabled = locked || !limits;
  $("layout-options").disabled = locked;
  $("auto-transliterate").disabled = locked || $("language").value === "en" || limits?.transliteration_configured === false;
  $("submit").disabled = locked || !limits || limits.translation_configured === false || !$("pdf-file").files.length || !$("file-error").hidden;
  $("submit-label").textContent = value ? "Working on your PDF…" : active ? "Translation in progress" : "Translate PDF";
  $("translate-form").setAttribute("aria-busy", String(value));
  $("resume").disabled = value;
  $("cancel").hidden = !active?.jobId || !["inspect","translate","preview","render"].includes(active.phase);
  $("cancel").disabled = Boolean(active?.cancelRequested);
  $("cancel").textContent = active?.cancelRequested ? "Cancelling…" : "Cancel translation";
  $("transliterate").disabled = locked;
  $("transliteration-choices").disabled = locked;
  const selected = document.querySelectorAll('#transliteration-words input:checked').length;
  for (const id of ["generate-transliteration","apply-transliteration"]) $(id).disabled = locked || !selected || selected > 20;
}
function layoutHelp() {
  $("layout-help").textContent = {
    auto:"Keeps simple text boxes fixed and reflows paragraphs made of four or more text spans. Dense pages may still need review.",
    spans:"Keeps each original box fixed; longer translations may shrink to fit.",
    paragraphs:"Paragraph reflow is experimental; dense pages can still need smaller text or review."
  }[document.querySelector('input[name="layout_mode"]:checked').value];
}
function fontNote() {
  if ($("language").value === "en") $("auto-transliterate").checked = false;
  $("auto-transliterate").disabled = busy || Boolean(active) || $("language").value === "en" || limits?.transliteration_configured === false;
  const missing = limits?.available_font_languages && !limits.available_font_languages.includes($("language").value);
  $("font-note").hidden = !missing;
  $("font-note").textContent = missing ? "This language’s font is not installed. Some text may stay in the source language; install its font before translating." : "";
}
function validateFile(file) {
  if (!file) return "";
  if (!/\.pdf$/i.test(file.name)) return "Choose a PDF file. Other file types are not supported.";
  if (!file.size) return "This file is empty. Choose a PDF with content.";
  if (limits && file.size > limits.max_upload_mb * 1024 * 1024) return `This file exceeds the ${limits.max_upload_mb} MB limit. Choose a smaller PDF or split it into parts.`;
  return "";
}
function selectFile() {
  const file = $("pdf-file").files[0];
  const error = validateFile(file);
  $("file-label").textContent = file ? file.name : "Choose a PDF or drop it here";
  $("file-detail").textContent = file ? `${file.size < 104858 ? (file.size / 1024).toFixed(0) + " KB" : (file.size / 1048576).toFixed(1) + " MB"} · PDF` : limits ? `Up to ${limits.max_upload_mb} MB · ${limits.max_pages} pages` : "Checking upload limits…";
  $("file-error").textContent = error;
  $("file-error").hidden = !error;
  $("pdf-file").setAttribute("aria-invalid", String(Boolean(error)));
  $("dropzone").classList.toggle("invalid", Boolean(error));
  setBusy(busy);
}
function progress(title, detail, step, value) {
  $("result").hidden = false;
  $("result").className = "result working";
  $("status-title").textContent = title;
  // Avoid repeatedly announcing an unchanged status at every poll.
  if ($("status-detail").textContent !== detail) $("status-detail").textContent = detail;
  $("stage").textContent = title;
  const index = ["upload","inspect","translate","render","done"].indexOf(step);
  document.querySelectorAll("[data-step]").forEach((item, i) => {
    item.className = i < index ? "done" : i === index ? "current" : "";
    if (i === index) item.setAttribute("aria-current", "step"); else item.removeAttribute("aria-current");
  });
  if (value == null) { $("progress").removeAttribute("value"); $("percent").textContent = "Waiting"; }
  else { $("progress").value = Math.max(0, Math.min(100, value)); $("percent").textContent = `${Math.round(value)}%`; }
}
function resetResult() {
  window.documentReview?.reset();
  for (const id of ["download","draft","review-note","resume","new-document","document-note","transliterate","transliteration-panel"]) $(id).hidden = true;
  $("review-reasons").replaceChildren();
  $("progress-wrap").hidden = false;
}
async function poll(jobId, phase) {
  while (true) {
    let job;
    for (let attempt = 0; ; attempt++) {
      try { job = await request(`/v1/jobs/${encodeURIComponent(jobId)}`); break; }
      catch (error) { if (!error.recoverable || attempt >= 2) throw error; await sleep(1000 * (attempt + 1)); }
    }
    const p = Math.max(0, Math.min(1, Number(job.progress) || 0));
    if (job.status === "cancelled") throw appError("job_cancelled", "Translation cancelled.");
    if (job.status === "cancelling" || active?.cancelRequested) progress("Cancelling translation", "Stopping requests and waiting for any current PDF operation to finish.", phase === "inspect" ? "inspect" : "translate", null);
    else if (job.status === "queued") progress("Waiting for your turn", "Your PDF is queued. Processing will begin when the current job finishes.", phase === "inspect" ? "inspect" : "translate", null);
    else if (phase === "preview") progress("Preparing pronunciations", "Generating native-script pronunciations for your selected words. Your PDF is unchanged until you apply them.", "translate", p * 100);
    else if (phase === "render") progress("Preparing your updated PDF", "Applying the selected pronunciations and checking the layout.", "render", 85 + p * 14);
    else if (phase === "inspect") progress("Reading your PDF", "Checking pages and extracting text. Scanned pages are read with OCR automatically.", "inspect", 5 + p * 20);
    else if (p >= .7) progress("Preparing your download", "Fitting translated text, rendering pages, and checking the result.", "render", 85 + (p - .7) / .3 * 14);
    else progress("Translating your document", "Translating text while checking formatting and protected numbers, links, and identifiers.", "translate", 25 + p / .7 * 60);
    if (job.status === "failed") throw appError(job.error_code || "processing_failed", job.error_message || "The document could not be processed.");
    if (["completed", "completed_with_issues"].includes(job.status)) return job;
    await sleep(1000);
  }
}
async function sourceNote() {
  const pages = await request(`/v1/documents/${active.documentId}/pages`);
  const scans = pages.filter(p => p.metadata_json?.extraction_method === "ocr").length;
  const blanks = pages.filter(p => p.metadata_json?.extraction_method === "blank").length;
  active.sourceNote = `${pages.length} page${pages.length === 1 ? "" : "s"}${scans ? ` · ${scans} scanned (OCR)` : " · Digital PDF"}${blanks ? ` · ${blanks} blank, preserved` : ""}`;
  saveActive();
  $("document-note").textContent = active.sourceNote;
  $("document-note").hidden = false;
}
const issueGroups = [
  {codes:["transliteration_incomplete"], text:"Pronunciation conversion was unavailable. The translated PDF is available with remaining English words unchanged."},
  {codes:["translation_truncated"], text:"Some translations remained incomplete after automatic recovery. Affected regions keep their source text and need review."},
  {codes:["passage_translation_split"], text:"Some translations were recovered using smaller passages. Review sentence flow and terminology."},
  {codes:["source_geometry_overlap"], text:"Some original text regions have overlapping boxes. Review these regions for visible overlap."},
  {codes:["small_text","font_size_inconsistent"], text:"Some text remains small or has uneven sizing after local consistency checks. Review these regions at reading size."},
  {codes:["unsupported_glyphs"], text:"Some characters could not be rendered with the installed fonts. Affected regions keep their source text."},
  {codes:["overflow","collision","clipping"], text:"Some text may be cramped, overlap, or exceed its available space. Check dense sections and tables."},
  {codes:["protected_value_changed","missing_translation"], text:"Some regions could not be translated safely and remain in the source language."},
  {codes:["inline_formatting_fallback"], text:"Some formatted passages were translated in smaller pieces. Check their formatting and sentence flow."},
  {codes:["ocr_layout_estimated","ocr_low_confidence","ocr_no_text"], text:"Scanned-page text or formatting is estimated. Check the transcription, spacing, and backgrounds."},
  {codes:["unsupported_ocr_notation"], text:"Some mathematical symbols could not be displayed correctly. Review the marked expressions."},
  {codes:["ocr_overlap_review_required"], text:"An overlapping OCR expression may have been misread or detected twice. Review it against the original scan."},
  {codes:["complex_table","unsupported_layout","unsegmented_table_row","signature_review_required"], text:"Complex layouts, tables, or signature regions may have been kept as they were."},
  {codes:["graphics_changed","page_dimensions_changed","missing_text"], text:"A rendering check found a possible change to content or graphics. Compare the output with your original."}
];
function showReview(issues, summary, status) {
  const retained = summary.retained_source_blocks;
  const needsReview = issues.length || retained || status === "completed_with_issues";
  $("review-note").hidden = !needsReview;
  if (!needsReview) return false;
  $("review-summary").textContent = `${retained ? `${retained} text region${retained === 1 ? " remains" : "s remain"} in the source language. ` : ""}${issues.length ? `${issues.length} review issue${issues.length === 1 ? " was" : "s were"} reported. ` : ""}Check the PDF before sharing it.`;
  const codes = new Set(issues.map(i => i.code));
  $("review-reasons").replaceChildren();
  for (const group of issueGroups) {
    if (!group.codes.some(c => codes.has(c))) continue;
    const item = document.createElement("li"); item.textContent = group.text;
    $("review-reasons").append(item);
  }
  return true;
}
async function finish() {
  const doc = await request(`/v1/documents/${active.documentId}`);
  const summary = doc.translation_summary;
  const empty = summary.total_blocks === 0;
  const needsReview = showReview(doc.issue_summary || [], summary, doc.status);
  const title = empty ? "PDF ready · no text found" : needsReview ? "Your PDF is ready for review" : "Your PDF is ready";
  const detail = empty ? "No translatable text was found. The original pages have been preserved." : `${summary.rendered_blocks} of ${summary.total_blocks} text regions rendered in ${names[active.language] || active.language}.`;
  progress(title, detail, "done", 100);
  $("result").className = needsReview ? "result warning" : "result success";
  $("progress-wrap").hidden = true;
  $("download").href = `/v1/documents/${active.documentId}/artifacts/final.pdf?v=${encodeURIComponent(active.jobId)}`;
  $("download").download = "";
  $("download").hidden = false;
  $("new-document").hidden = false;
  $("status-title").focus();
  finishedDocument = {documentId:active.documentId,language:active.language,layout:active.layout};
  window.documentReview?.offer(active.documentId);
  $("transliterate").hidden = empty || active.language === "en" || limits?.transliteration_configured === false;
  active = null; saveActive();
}
function failureMessage(error) {
  const messages = {
    encrypted_pdf:"This PDF is password-protected. Save an unlocked copy, then upload it again.",
    invalid_pdf:"This file could not be read as a PDF. Try opening it locally and exporting a fresh PDF copy.",
    upload_too_large:`This PDF exceeds the ${limits?.max_upload_mb || 25} MB limit. Compress it or split it into smaller PDFs.`,
    too_many_pages:`This PDF exceeds the ${limits?.max_pages || 100}-page limit. Split it into smaller PDFs.`,
    missing_api_key:"The translation service is not configured. Add your Bodhan credentials and restart the server.",
    invalid_api_key:"The translation service rejected its credentials. Check the Bodhan account configuration before trying again.",
    missing_ocr_api_key:"Scanned pages need OCR access. Configure a Bodhan key with OCR access, then try again.",
    ocr_authentication_failed:"This PDF needs OCR, but the configured account does not have access. Check its OCR credentials.",
    ocr_unavailable:"The OCR service could not finish after retries. Try again when the service is available.",
    invalid_ocr_response:"OCR returned incomplete or unreadable page information. Try a clearer scan or retry later.",
    ocr_request_rejected:"The OCR service rejected a scanned page. Check the source scan and your account access.",
    provider_unavailable:"The translation service could not finish after retries. Your original is unchanged; try again later.",
    provider_request_rejected:"The translation service rejected this request. Check your account access or try a smaller document.",
    reconstruction_failed:"No translated text could be rendered safely. A source-retaining draft is available below; it is not a completed translation.",
    unsupported_language:"This language is not supported by the server. Refresh the page and choose a listed language.",
    invalid_provider_response:"The translation service returned an unreadable response. Your original is unchanged; try again later."
  };
  return messages[error.code] || error.message || "Something went wrong while processing the PDF. Your original is unchanged.";
}
function showFailure(error) {
  if (active?.phase === "preview" && !error.recoverable) {
    finishedDocument = {documentId:active.documentId,language:active.language,layout:active.layout};
    $("download").href = `/v1/documents/${active.documentId}/artifacts/final.pdf`;
    $("download").hidden = false;
    active = null; previewRequest = null; previewJobId = null; saveActive();
    $("progress-wrap").hidden = true; $("cancel").hidden = true;
    $("status-title").textContent = "Pronunciation preview could not finish";
    $("status-detail").textContent = failureMessage(error) + " Your translated PDF is unchanged and available below.";
    $("transliteration-message").textContent = failureMessage(error) + " You can retry the preview.";
    $("generate-transliteration").hidden = false;
    $("apply-transliteration").hidden = true;
    $("new-document").hidden = false;
    return;
  }
  if (error.code === "job_cancelled") {
    active = null; saveActive(); resetResult();
    $("result").hidden = false; $("result").className = "result";
    $("status-title").textContent = "Translation cancelled";
    $("status-detail").textContent = "Processing has stopped. You can start another translation.";
    $("progress-wrap").hidden = true; $("cancel").hidden = true;
    $("new-document").hidden = false; $("status-title").focus();
    return;
  }
  $("result").hidden = false; $("result").className = "result error";
  $("status-title").textContent = error.recoverable && active ? "Connection interrupted" : "Couldn’t finish this PDF";
  $("status-detail").textContent = failureMessage(error);
  $("progress-wrap").hidden = true;
  $("download").hidden = true;
  $("resume").hidden = !(error.recoverable && active);
  if (error.recoverable && active) $("status-detail").textContent += " Reconnect to check this same job without starting another translation.";
  if (error.code === "reconstruction_failed" && active) {
    window.documentReview?.offer(active.documentId);
    $("draft").href = `/v1/documents/${active.documentId}/artifacts/draft.pdf`; $("draft").download = ""; $("draft").hidden = false;
  }
  if (!error.recoverable) { active = null; saveActive(); $("new-document").hidden = false; }
  $("status-title").focus();
}
async function runActive() {
  if (busy || !active) return;
  $("resume").hidden = true; setBusy(true);
  try {
    if (active.sourceNote) { $("document-note").textContent = active.sourceNote; $("document-note").hidden = false; }
    if (active.phase === "inspect") {
      await poll(active.jobId, "inspect");
      if (active.cancelRequested) throw appError("job_cancelled", "Translation cancelled.");
      await sourceNote();
      if (active.cancelRequested) throw appError("job_cancelled", "Translation cancelled.");
      active.phase = "start"; saveActive(); setBusy(true);
    }
    if (active.phase === "start") {
      // A lost POST response must not cause a duplicate billable translation.
      const doc = await request(`/v1/documents/${active.documentId}`);
      let translated = doc.latest_job?.operation === "translate" ? {job_id:doc.latest_job.id} : null;
      if (!translated) {
        try { translated = await request(`/v1/documents/${active.documentId}/translations`, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({target_language:active.language,script:"native",layout_mode:active.layout,transliterate:Boolean(active.transliterate)})}); }
        catch (error) {
          if (error.code !== "document_busy") throw error;
          const current = await request(`/v1/documents/${active.documentId}`);
          if (current.latest_job?.operation !== "translate") throw error;
          translated = {job_id:current.latest_job.id};
        }
      }
      active.jobId = translated.job_id; active.phase = "translate"; saveActive(); setBusy(true);
    }
    if (active.phase === "translate") { await poll(active.jobId, "translate"); active.phase = "result"; saveActive(); }
    if (active.phase === "preview") {
      const job = await poll(active.jobId, "preview");
      finishedDocument = {documentId:active.documentId,language:active.language,layout:active.layout};
      showPronunciations(job);
      active = null; saveActive();
      return;
    }
    if (active.phase === "render") { await poll(active.jobId, "render"); active.phase = "result"; saveActive(); }
    await finish();
  } catch (error) { showFailure(error); }
  finally { setBusy(false); }
}
function showWords(items, pronunciations = false) {
  $("transliteration-words").replaceChildren();
  for (const item of items) {
    const row=document.createElement("label"); row.className="transliteration-word";
    const box=document.createElement("input"); box.type="checkbox"; box.value=item.word; box.checked=pronunciations;
    const label=document.createElement("span"); label.textContent=item.word;
    if (pronunciations) {
      label.append(document.createTextNode(" → "));
      const native=document.createElement("bdi"); native.textContent=item.replacement; label.append(native);
    } else label.append(document.createTextNode(` · ${item.count} occurrence${item.count === 1 ? "" : "s"}`));
    row.append(box,label); $("transliteration-words").append(row);
    box.addEventListener("change",()=>setBusy(busy));
  }
}
function selectedWords() { return [...document.querySelectorAll('#transliteration-words input:checked')].map(box=>box.value); }
function showPronunciations(job) {
  previewJobId=job.id;
  $("result").className="result"; $("progress-wrap").hidden=true;
  $("status-title").textContent="Review the pronunciations";
  $("status-detail").textContent="Your PDF is unchanged. Uncheck any pronunciation you do not want, then apply the selected changes.";
  $("transliteration-panel").hidden=false; $("transliterate").hidden=true;
  $("generate-transliteration").hidden=true; $("apply-transliteration").hidden=false;
  $("transliteration-message").textContent="Review the suggested spellings; model pronunciations may be approximate.";
  showWords(job.payload.suggestions,true);
  $("status-title").focus();
}
$("transliterate").addEventListener("click",async()=>{
  if (busy || active || !finishedDocument) return;
  setBusy(true);
  try {
    const response=await request(`/v1/documents/${finishedDocument.documentId}/transliteration-candidates`);
    previewJobId=null; previewRequest=null; showWords(response.candidates);
    $("transliteration-panel").hidden=false;
    $("generate-transliteration").hidden=!response.candidates.length; $("apply-transliteration").hidden=true;
    $("transliteration-message").textContent=response.candidates.length ? "Select the words you want to transliterate. Nothing is selected automatically." : "No eligible English words remain in the rendered text.";
    $("transliteration-title").setAttribute("tabindex","-1"); $("transliteration-title").focus();
  } catch(error) { $("status-detail").textContent=failureMessage(error); }
  finally { setBusy(false); }
});
$("generate-transliteration").addEventListener("click",async()=>{
  if (busy || active || !finishedDocument) return;
  const words=selectedWords(); if (!words.length || words.length>20) return;
  const selection=JSON.stringify([finishedDocument.documentId,...words]);
  if (previewRequest?.selection !== selection) previewRequest={selection,id:Array.from(crypto.getRandomValues(new Uint8Array(16)),n=>n.toString(16).padStart(2,'0')).join('')};
  setBusy(true);
  try {
    const result=await request(`/v1/documents/${finishedDocument.documentId}/transliteration-preview`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({words,request_id:previewRequest.id})});
    active={...finishedDocument,jobId:result.job_id,phase:"preview"}; saveActive();
    $("progress-wrap").hidden=false;
  } catch(error) { $("transliteration-message").textContent=failureMessage(error); }
  finally { setBusy(false); }
  if (active) await runActive();
});
$("apply-transliteration").addEventListener("click",async()=>{
  if (busy || active || !finishedDocument || !previewJobId) return;
  const words=selectedWords(); if (!words.length || words.length>20) return;
  setBusy(true);
  try {
    const result=await request(`/v1/documents/${finishedDocument.documentId}/transliteration-apply`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({preview_job_id:previewJobId,words})});
    active={...finishedDocument,jobId:result.job_id,phase:"render"}; saveActive();
    resetResult();
  } catch(error) { $("transliteration-message").textContent=failureMessage(error); }
  finally { setBusy(false); }
  if (active) await runActive();
});
$("pdf-file").addEventListener("change", () => { if (!active) { $("result").hidden = true; window.documentReview?.reset(); } selectFile(); });
$("language").addEventListener("change", fontNote);
for (const radio of document.querySelectorAll('input[name="layout_mode"]')) radio.addEventListener("change", layoutHelp);
for (const event of ["dragenter","dragover"]) $("dropzone").addEventListener(event, e => { e.preventDefault(); if (!busy && !active) $("dropzone").classList.add("dragover"); });
for (const event of ["dragleave","drop"]) $("dropzone").addEventListener(event, e => { e.preventDefault(); $("dropzone").classList.remove("dragover"); });
$("dropzone").addEventListener("drop", e => {
  if (busy || active || !e.dataTransfer.files.length) return;
  if (e.dataTransfer.files.length !== 1) { $("file-error").textContent = "Choose one PDF at a time."; $("file-error").hidden = false; setBusy(false); return; }
  const transfer = new DataTransfer(); transfer.items.add(e.dataTransfer.files[0]); $("pdf-file").files = transfer.files;
  $("result").hidden = true; window.documentReview?.reset(); selectFile();
});
$("translate-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (busy || active || !limits || limits.translation_configured === false) return;
  const file = $("pdf-file").files[0];
  if (!file) return;
  selectFile(); if (validateFile(file)) return;
  resetResult(); setBusy(true);
  const language = $("language").value, layout = document.querySelector('input[name="layout_mode"]:checked').value;
  try {
    progress("Uploading your PDF", "Sending your document for processing. Keep this page open until the upload finishes.", "upload", null);
    $("status-title").focus();
    const data = new FormData(); data.append("file",file);
    const upload = await request("/v1/documents", {method:"POST",body:data});
    active = {documentId:upload.document_id,jobId:upload.job_id,phase:"inspect",language,layout,transliterate:$("auto-transliterate").checked}; saveActive();
  } catch (error) { showFailure(error); }
  finally { setBusy(false); }
  if (active) await runActive();
});
$("resume").addEventListener("click", runActive);
$("cancel").addEventListener("click", async () => {
  if (!active?.jobId || active.cancelRequested) return;
  const current = active;
  current.cancelRequested = true; saveActive(); setBusy(busy);
  try {
    const job = await request(`/v1/jobs/${encodeURIComponent(current.jobId)}/cancel`, {method:"POST"});
    if (active !== current) return;
    // A reconnect is needed if polling had stopped after a network failure.
    if (!busy) await runActive();
  } catch (error) {
    if (active !== current) return;
    current.cancelRequested = false; saveActive(); setBusy(busy);
    $("status-detail").textContent = "Could not confirm cancellation. The job may still be running; try Cancel again or reconnect.";
  }
});
$("new-document").addEventListener("click", () => {
  if (busy || active) return;
  finishedDocument=null; previewJobId=null;
  $("pdf-file").value = ""; $("result").hidden = true; resetResult(); layoutHelp(); fontNote(); selectFile(); $("pdf-file").focus();
});
async function connect() {
  $("reconnect").disabled = true;
  try {
    limits = await request("/v1/health");
    $("language").replaceChildren();
    for (const code of [...limits.supported_languages].sort((a,b) => (names[a] || a).localeCompare(names[b] || b))) {
      const option = document.createElement("option"); option.value = code;
      option.textContent = names[code] === nativeNames[code] ? names[code] : `${names[code] || code} · ${nativeNames[code] || code}`;
      $("language").append(option);
    }
    $("language").value = active && limits.supported_languages.includes(active.language) ? active.language : limits.supported_languages.includes("hi") ? "hi" : limits.supported_languages[0];
    if (active) {
      const choice = [...document.querySelectorAll('input[name="layout_mode"]')].find(radio => radio.value === active.layout);
      if (choice) choice.checked = true;
      layoutHelp();
    }
    $("connection-note").hidden = limits.translation_configured !== false;
    $("connection-message").textContent = "The translation service is not configured. Configure Bodhan and restart the server to translate PDFs.";
    $("reconnect").textContent = "Check configuration";
    selectFile(); fontNote();
    if (active && !busy) { resetResult(); await runActive(); }
  } catch (error) {
    $("connection-note").hidden = false;
    $("connection-message").textContent = "Cannot connect to Translation Studio. Start the server, then check the connection again.";
    $("reconnect").textContent = "Check connection";
    limits = null; setBusy(false);
  } finally { $("reconnect").disabled = false; }
}
$("reconnect").addEventListener("click", connect);
layoutHelp(); connect();
