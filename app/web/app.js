"use strict";
const $ = id => document.getElementById(id);
const names = {as:"Assamese",bn:"Bengali",brx:"Bodo",doi:"Dogri",en:"English",gu:"Gujarati",hi:"Hindi",kn:"Kannada",kok:"Konkani",ks:"Kashmiri (Arabic)","ks-Deva":"Kashmiri (Devanagari)",mai:"Maithili",ml:"Malayalam",mni:"Manipuri (Meitei)","mni-Beng":"Manipuri (Bengali)",mr:"Marathi",ne:"Nepali",or:"Odia",pa:"Punjabi",sa:"Sanskrit",sat:"Santali",sd:"Sindhi (Devanagari)","sd-Arab":"Sindhi (Arabic)",ta:"Tamil",te:"Telugu",ur:"Urdu"};
let limits = null, busy = false;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function request(url, options = {}) {
  const response = await fetch(url, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error?.message || `Request failed (${response.status})`);
  return body;
}
function setBusy(value) {
  busy = value;
  $("pdf-file").disabled = value;
  $("language").disabled = value || !limits;
  $("submit").disabled = value || !limits;
  $("submit").firstChild.textContent = value ? "Translating… " : "Translate PDF ";
}
function progress(title, detail, stage, value) {
  $("result").hidden = false;
  $("result").className = "result working";
  $("status-title").textContent = title;
  $("status-detail").textContent = detail;
  $("stage").textContent = stage;
  $("progress").value = value;
  $("percent").textContent = `${Math.round(value)}%`;
}
function selectFile() {
  const file = $("pdf-file").files[0];
  $("file-label").textContent = file ? file.name : "Choose a PDF or drop it here";
  $("file-detail").textContent = file ? `${(file.size / 1024 / 1024).toFixed(1)} MB · PDF` : `PDF · Up to ${limits?.max_upload_mb || 25} MB / ${limits?.max_pages || 100} pages`;
}
async function poll(jobId, phase) {
  while (true) {
    let job;
    for (let attempt = 0; ; attempt++) {
      try { job = await request(`/v1/jobs/${encodeURIComponent(jobId)}`); break; }
      catch (error) { if (attempt >= 2) throw error; await sleep(1000 * (attempt + 1)); }
    }
    const inspecting = phase === "inspect";
    progress(inspecting ? "Reading your PDF" : "Translating your document", inspecting ? "Extracting text and checking the pages." : "Translating text and preparing your download. This may take a few minutes.", inspecting ? "Reading PDF" : "Translation & rendering", inspecting ? 5 + job.progress * 20 : 25 + job.progress * 70);
    if (job.status === "failed") {
      const error = new Error(job.error_message || "The document could not be processed.");
      error.code = job.error_code;
      throw error;
    }
    if (["completed", "completed_with_issues"].includes(job.status)) return job;
    await sleep(1000);
  }
}
$("pdf-file").addEventListener("change", selectFile);
for (const event of ["dragenter", "dragover"]) $("dropzone").addEventListener(event, e => { e.preventDefault(); if (!busy) $("dropzone").classList.add("dragover"); });
for (const event of ["dragleave", "drop"]) $("dropzone").addEventListener(event, e => { e.preventDefault(); $("dropzone").classList.remove("dragover"); });
$("dropzone").addEventListener("drop", e => {
  if (busy || !e.dataTransfer.files.length) return;
  const transfer = new DataTransfer();
  transfer.items.add(e.dataTransfer.files[0]);
  $("pdf-file").files = transfer.files;
  selectFile();
});
$("translate-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (busy || !limits) return;
  const file = $("pdf-file").files[0];
  if (!file) return;
  $("download").hidden = $("draft").hidden = $("review-note").hidden = true;
  $("progress-wrap").hidden = false;
  let documentId;
  try {
    if (!file.name.toLowerCase().endsWith(".pdf")) throw new Error("Please choose a PDF file.");
    if (file.size > limits.max_upload_mb * 1024 * 1024) throw new Error(`Choose a PDF smaller than ${limits.max_upload_mb} MB.`);
    setBusy(true);
    progress("Uploading your PDF", file.name, "Uploading", 2);
    const data = new FormData();
    data.append("file", file);
    const upload = await request("/v1/documents", {method:"POST",body:data});
    documentId = upload.document_id;
    await poll(upload.job_id, "inspect");
    const translated = await request(`/v1/documents/${documentId}/translations`, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({target_language:$("language").value,script:"native"})});
    const job = await poll(translated.job_id, "translate");
    const document = await request(`/v1/documents/${documentId}`);
    const summary = document.translation_summary;
    const issues = document.issue_summary.length;
    progress("Your PDF is ready", `${summary.rendered_blocks} of ${summary.total_blocks} text regions rendered.`, "Complete", 100);
    $("result").className = "result";
    if (issues || job.status === "completed_with_issues" || summary.retained_source_blocks) {
      $("review-note").textContent = `${summary.retained_source_blocks} text regions remain in the source language. ${issues} review issues reported. Check the downloaded PDF before using it.`;
      $("review-note").hidden = false;
    }
    $("download").href = `/v1/documents/${documentId}/artifacts/final.pdf`;
    $("download").download = ""; // Use the server's original-name + language filename.
    $("download").hidden = false;
  } catch (error) {
    $("result").hidden = false;
    $("result").className = "result error";
    $("status-title").textContent = "Couldn’t finish this PDF";
    $("status-detail").textContent = error.message;
    $("progress-wrap").hidden = true;
    if (error.code === "reconstruction_failed" && documentId) {
      $("draft").href = `/v1/documents/${documentId}/artifacts/draft.pdf`;
      $("draft").download = "";
      $("draft").hidden = false;
    }
  } finally { setBusy(false); }
});
request("/v1/health").then(health => {
  limits = health;
  $("language").replaceChildren();
  for (const code of [...health.supported_languages].sort((a,b) => (names[a] || a).localeCompare(names[b] || b))) {
    const option = document.createElement("option"); option.value = code; option.textContent = names[code] || code; $("language").append(option);
  }
  $("language").value = health.supported_languages.includes("hi") ? "hi" : health.supported_languages[0];
  selectFile(); setBusy(false);
}).catch(() => {
  $("result").hidden = false; $("result").className = "result error";
  $("status-title").textContent = "Cannot connect to Translation Studio";
  $("status-detail").textContent = "Start the server, then reload this page.";
  $("progress-wrap").hidden = true;
});
