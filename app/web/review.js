/* Review decisions triage findings; underlying diagnostics remain available. */
(() => {
  "use strict";
  const el = id => document.getElementById(id);
  const labels = {change:"Needs attention", review:"Check visually", clear:"No automatic issue", okay:"You marked okay"};
  const symbols = {change:"!", review:"△", clear:"○", okay:"✓"};
  let documentId = null, snapshot = null, selected = null, pageNumber = 1;
  let decisions = {}, generation = 0, scrollLock = false, repairing = false, repairRun = 0;
  const guidance = {
    overflow:"Compare the retained passage with the original. Its translation could not fit safely.",
    unsupported_glyphs:"Check for missing characters. Install the required font before regenerating the PDF.",
    missing_text:"Compare the full passage for missing or clipped words; extracted text can differ from visible text.",
    collision:"Check whether nearby passages visibly overlap or obscure each other.",
    source_geometry_overlap:"The original boxes overlap too. Check the visible lettering on both pages.",
    protected_value_changed:"Check every number, identifier and link against the original before using this output.",
    small_text:"Zoom in, then check readability at the size you intend to read or print.",
    font_size_inconsistent:"Compare neighbouring text sizes and check that headings and body text remain distinct.",
    ocr_layout_estimated:"Check transcription, backgrounds and text placement against the scan.",
    graphics_changed:"Compare images, table borders and graphics outside the translated text.",
    passage_translation_split:"Read the whole sentence for continuity and consistent terminology.",
    inline_formatting_fallback:"Check sentence flow and the placement of bold, italic and other formatting."
  };
  function textNode(tag, text, className) {
    const node = document.createElement(tag); node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  const storageKey = () => `translation-studio-review:${documentId}`;
  function saveDecisions() {
    try { sessionStorage.setItem(storageKey(), JSON.stringify({version:snapshot.version, decisions})); }
    catch { el("review-counts").textContent += " Review marks could not be saved in this tab."; }
  }
  function reset() {
    generation++; repairRun++; repairing = false; documentId = null; snapshot = null; decisions = {}; selected = null;
    el("document-review").hidden = true; el("see-issues").hidden = true;
    el("see-issues").setAttribute("aria-expanded", "false");
    for (const side of ["source", "translated"]) {
      el(`review-${side}`).removeAttribute("src");
      el(`${side}-highlights`).replaceChildren();
    }
  }
  function offer(id) { reset(); documentId = id; el("see-issues").hidden = false; }
  function colour(region) { return decisions[region.id] === "okay" ? "okay" : decisions[region.id] === "change" ? "change" : region.status; }
  function statusLabel(region) {
    return decisions[region.id] === "change" ? "Queued for changes" : labels[colour(region)];
  }
  function regionLabel(region) {
    const index = snapshot.regions.indexOf(region) + 1;
    return region.source_rects.length ? `Region ${index}` : region.page_number ? "Page finding" : "Document finding";
  }
  function matches(region, filter) {
    if (filter === "all") return true;
    if (filter === "okay") return decisions[region.id] === "okay";
    if (filter === "human-change") return decisions[region.id] === "change";
    if (decisions[region.id]) return false;
    return filter === "flagged" ? region.status !== "clear" : region.status === filter;
  }
  function refreshSelection() {
    const filter = el("review-filter").value;
    const current = snapshot.regions.find(r => r.id === selected);
    if (!current || !matches(current, filter)) {
      selected = snapshot.regions.find(r =>
        (r.page_number === pageNumber || r.page_number == null) && matches(r, filter))?.id || null;
    }
    regionList(); detail(); highlights();
  }
  function overview() {
    const counts = {change:0, review:0, clear:0};
    const textRegions = snapshot.regions.filter(r => r.source_rects.length);
    textRegions.filter(r => !decisions[r.id]).forEach(r => counts[r.status]++);
    el("review-bar").replaceChildren();
    for (const status of ["change", "review", "clear"]) {
      if (!counts[status]) continue;
      const segment = textNode("button", String(counts[status]), `bar-${status}`);
      segment.type = "button"; segment.style.flexGrow = counts[status];
      segment.title = `${counts[status]} unmarked text regions: ${labels[status]}`;
      segment.setAttribute("aria-label", segment.title);
      segment.addEventListener("click", () => {
        el("review-filter").value = status === "clear" ? "all" : status;
        choose(snapshot.regions.find(r => r.source_rects.length && !decisions[r.id] && r.status === status).id, true);
      });
      el("review-bar").append(segment);
    }
    el("review-bar").setAttribute("aria-label", `${counts.change} need attention, ${counts.review} to check visually, ${counts.clear} with no automatic issue. Unmarked text regions only. Select a segment to inspect.`);
    const okay = Object.values(decisions).filter(s => s === "okay").length;
    const changes = Object.values(decisions).filter(s => s === "change").length;
    const unlocated = snapshot.regions.filter(r => !r.source_rects.length).length;
    el("review-counts").textContent = `${snapshot.regions.filter(r => r.status !== "clear" && !decisions[r.id]).length} pending findings · ${counts.change} text regions need attention · ${counts.review} text regions to check visually${unlocated ? ` · ${unlocated} page/document finding groups in total` : ""}. Reviewed: ${okay} marked okay · ${changes} queued for changes.`;
    repairControls();
    for (const option of el("review-page").options) {
      const pending = snapshot.regions.filter(r => r.page_number === Number(option.value) && matches(r, "flagged")).length;
      option.textContent = `${option.value}${pending ? ` · ${pending} pending` : ""}`;
    }
  }
  const repairStorageKey = () => `translation-studio-repair:${documentId}`;
  function repairControls() {
    if (!snapshot) return;
    const count = Object.values(decisions).filter(s => s === "change").length;
    el("repair-queue-count").textContent = `${count} region${count === 1 ? "" : "s"} queued for changes. Regions marked okay are excluded.`;
    el("repair-marked").disabled = repairing || !count;
    el("repair-instruction").disabled = repairing;
    for (const id of ["review-okay", "review-change", "review-reset"]) el(id).disabled = repairing || (id === "review-reset" && !decisions[selected]);
    const link = el("review-final-download");
    link.textContent = snapshot.output === "draft.pdf" ? "Download draft PDF · source retained" : "Download final PDF";
    link.setAttribute("aria-disabled", String(repairing));
    if (repairing) { link.removeAttribute("href"); link.tabIndex = -1; }
    else { link.href = `/v1/documents/${encodeURIComponent(documentId)}/artifacts/${snapshot.output}?v=${encodeURIComponent(snapshot.version)}`; link.removeAttribute("tabindex"); }
    el("review-previous-download").hidden = repairing || !snapshot.previous_available;
    el("review-previous-download").href = `/v1/documents/${encodeURIComponent(documentId)}/artifacts/previous.pdf?v=${encodeURIComponent(snapshot.version)}`;
  }
  function showRepairResults(results) {
    el("repair-results").replaceChildren();
    for (const result of results || []) {
      const region = snapshot?.regions.find(r => r.id === result.block_id);
      const label = region ? regionLabel(region) : "Selected finding";
      const status = {repaired:"Repaired — review again", rejected:"Previous region kept", no_change:"No change suggested", manual_review:"Needs manual review"}[result.status] || result.status;
      el("repair-results").append(textNode("li", `${label}: ${status}. ${result.reason || ""}`));
    }
  }
  async function runRepair(saved = null) {
    if (repairing || (!snapshot && !saved)) return;
    const id = documentId, ticket = generation, run = ++repairRun;
    const body = saved?.body || {version:snapshot.version, request_id:globalThis.crypto?.randomUUID?.() || `repair-${Date.now()}-${Math.random().toString(36).slice(2)}`,
      region_ids:Object.keys(decisions).filter(key => decisions[key] === "change"), instruction:el("repair-instruction").value.trim()};
    if (!body.region_ids.length) return;
    let pending = saved || {body, decisions};
    el("review-content").hidden = false;
    repairing = true; repairControls();
    el("repair-status").textContent = "Submitting selected regions for AI repair…";
    el("repair-results").replaceChildren();
    try {
      try { sessionStorage.setItem(repairStorageKey(), JSON.stringify(pending)); } catch {}
      if (!pending.job_id) {
        const response = await request(`/v1/documents/${encodeURIComponent(id)}/review/repair`,
          {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
        pending.job_id = response.job_id;
        try { sessionStorage.setItem(`translation-studio-repair:${id}`, JSON.stringify(pending)); } catch {}
      }
      let job;
      while (true) {
        if (ticket !== generation || id !== documentId) return;
        job = await request(`/v1/jobs/${encodeURIComponent(pending.job_id)}`);
        if (["completed", "completed_with_issues", "failed", "cancelled"].includes(job.status)) break;
        el("repair-status").textContent = `Repairing marked regions… ${Math.round((job.progress || 0)*100)}%`;
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
      if (ticket !== generation || id !== documentId) return;
      try { sessionStorage.removeItem(`translation-studio-repair:${id}`); } catch {}
      if (job.status === "failed" || job.status === "cancelled") {
        showRepairResults(job.payload?.repair_results);
        throw new Error(job.error_message || "Repair stopped. The previous PDF is retained.");
      }
      // Refreshing the output invalidates old review marks only when its version
      // changed. Failed/no-change regions are queued for another inspection.
      const oldDecisions = {...decisions};
      const instruction = body.instruction;
      repairing = false;
      await open();
      if (id !== documentId) return;
      if (job.payload?.accepted) {
        const outcomes = new Map((job.payload.repair_results || []).map(r => [r.block_id,r.status]));
        decisions = Object.fromEntries(Object.entries(oldDecisions).filter(([key,value]) =>
          value === "change" && outcomes.get(key) !== "repaired"));
        saveDecisions(); overview(); refreshSelection();
      }
      el("repair-instruction").value = instruction;
      showRepairResults(job.payload?.repair_results);
      el("repair-status").textContent = `${job.payload?.accepted || 0} region${job.payload?.accepted === 1 ? "" : "s"} repaired. Review the result, then download the PDF below.`;
    } catch (error) {
      if (ticket !== generation || id !== documentId) return;
      el("repair-status").textContent = error.message + (pending.job_id || error.recoverable ? " Reopen this review to reconnect to the same request." : "");
      if (!pending.job_id && !error.recoverable) {
        try { sessionStorage.removeItem(`translation-studio-repair:${id}`); } catch {}
      }
    } finally {
      if (id === documentId && run === repairRun) { repairing = false; repairControls(); }
    }
  }
  function regionList() {
    const filter = el("review-filter").value;
    const items = snapshot.regions.filter(r => (r.page_number === pageNumber || r.page_number == null) && matches(r, filter));
    el("review-region-list").replaceChildren();
    el("review-list-count").textContent = `${items.length} matching region${items.length === 1 ? "" : "s"} on this page${snapshot.regions.some(r => r.page_number == null) ? " / document-wide" : ""}`;
    if (!items.length) el("review-region-list").append(textNode("p", filter === "flagged" ? "No pending findings on this page. Review another page, or choose Marked okay / Needs changes queue to revisit decisions." : "No matching regions on this page. Try All regions or another page.", "field-hint"));
    for (const region of items) {
      const status = colour(region);
      const button = textNode("button", "", `review-region region-${status}`);
      button.type = "button"; button.dataset.regionId = region.id;
      button.setAttribute("aria-pressed", String(selected === region.id));
      button.append(textNode("strong", `${symbols[status]} ${regionLabel(region)} · ${statusLabel(region)}`));
      button.append(textNode("span", region.issues.length ? region.issues.map(i => i.code.replaceAll("_", " ")).join(" · ") : region.rendered === false ? "Source text retained" : "Check meaning against the original"));
      button.addEventListener("click", () => choose(region.id, true));
      el("review-region-list").append(button);
    }
  }
  function detail() {
    const region = snapshot.regions.find(r => r.id === selected);
    el("review-detail").hidden = !region;
    if (!region) return;
    el("review-detail-title").textContent = `${regionLabel(region)}${region.page_number ? ` · page ${region.page_number}` : ""}`;
    el("review-region-status").textContent = `Automatic: ${labels[region.status]}.${region.rendered === false ? " Source text is retained in the output. The candidate translation below may not be visible in the PDF." : ""}${!region.source_rects.length ? " No precise region was reported; inspect the whole page or document." : ""}`;
    el("review-findings").replaceChildren();
    for (const issue of region.issues) {
      const item = document.createElement("div"); item.className = "review-finding";
      item.append(textNode("strong", issue.code.replaceAll("_", " ")),
                  textNode("p", issue.message),
                  textNode("p", guidance[issue.code] || "Compare the affected content with the original before deciding whether changes are needed.", "field-hint"));
      el("review-findings").append(item);
    }
    el("review-source-text").textContent = region.source_text || "No passage was attached to this finding.";
    el("review-translated-text").textContent = region.translated_text || "No candidate translation available.";
    el("review-okay").setAttribute("aria-pressed", String(decisions[region.id] === "okay"));
    el("review-change").setAttribute("aria-pressed", String(decisions[region.id] === "change"));
    el("review-reset").disabled = repairing || !decisions[region.id];
  }
  function highlights() {
    const page = snapshot.pages.find(p => p.number === pageNumber);
    for (const side of ["source", "translated"]) {
      const layer = el(`${side}-highlights`); layer.replaceChildren();
      layer.hidden = !el("review-highlights").checked || el(`${side}-canvas`).hidden;
      for (const region of snapshot.regions.filter(r => r.page_number === pageNumber && matches(r, el("review-filter").value))) {
        for (const rect of region[`${side}_rects`]) {
          const status = colour(region);
          const marker = textNode("button", "", `region-marker region-${status}${region.id === selected ? " selected" : ""}`);
          marker.type = "button"; marker.dataset.regionId = region.id;
          const label = `${symbols[status]} ${regionLabel(region)}: ${statusLabel(region)}`;
          marker.title = label; marker.setAttribute("aria-label", label);
          marker.setAttribute("aria-pressed", String(region.id === selected));
          Object.assign(marker.style, {left:`${rect[0]/page.width*100}%`,top:`${rect[1]/page.height*100}%`,width:`${(rect[2]-rect[0])/page.width*100}%`,height:`${(rect[3]-rect[1])/page.height*100}%`});
          marker.addEventListener("click", () => {
            const filter = el("review-filter").value;
            if (!matches(region, filter)) el("review-filter").value = "all";
            choose(region.id, false); el("review-detail-title").focus({preventScroll:true});
          });
          layer.append(marker);
        }
      }
    }
  }
  function zoom() {
    for (const side of ["source", "translated"]) el(`${side}-canvas`).style.width = `${Number(el("review-zoom").value)*100}%`;
  }
  function loadPage() {
    const page = snapshot.pages.find(p => p.number === pageNumber);
    if (!page) return;
    el("review-page").value = pageNumber;
    el("review-prev").disabled = pageNumber === snapshot.pages[0].number;
    el("review-next").disabled = pageNumber === snapshot.pages.at(-1).number;
    for (const side of ["source", "translated"]) {
      const url = page[side === "source" ? "source_preview" : "translated_preview"];
      const image = el(`review-${side}`), canvas = el(`${side}-canvas`);
      canvas.hidden = !url; canvas.style.aspectRatio = `${page.width} / ${page.height}`;
      el(`${side}-unavailable`).hidden = Boolean(url);
      image.alt = `${side === "source" ? "Original" : snapshot.output === "draft.pdf" ? "Source-retaining draft" : "Translated"} PDF, page ${pageNumber}`;
      image.onerror = () => { canvas.hidden = true; el(`${side}-unavailable`).hidden = false; el(`${side}-highlights`).hidden = true; };
      if (url) image.src = `${url}?v=${encodeURIComponent(snapshot.version)}`;
      else image.removeAttribute("src");
      el(`${side}-scroll`).scrollTo(0,0);
    }
    zoom();
  }
  function centerRegion(region) {
    const page = snapshot.pages.find(p => p.number === pageNumber);
    for (const side of ["source", "translated"]) {
      const rect = region[`${side}_rects`][0];
      if (!rect) continue;
      const scroller = el(`${side}-scroll`), canvas = el(`${side}-canvas`);
      scroller.scrollTo({left:(rect[0]+rect[2])/2/page.width*canvas.clientWidth-scroller.clientWidth/2,
                         top:(rect[1]+rect[3])/2/page.height*canvas.clientHeight-scroller.clientHeight/2});
    }
  }
  function choose(id, center) {
    const region = snapshot.regions.find(r => r.id === id);
    if (!region) return;
    selected = id;
    if (region.page_number && region.page_number !== pageNumber) { pageNumber = region.page_number; loadPage(); }
    regionList(); detail(); highlights();
    if (center) { centerRegion(region); el("review-detail-title").focus({preventScroll:true}); }
  }
  function changePage(number) {
    pageNumber = number;
    selected = null; loadPage(); regionList(); detail(); highlights();
  }
  async function open() {
    if (!documentId) return;
    const ticket = ++generation, id = documentId;
    el("document-review").hidden = false; el("review-content").hidden = true;
    el("see-issues").setAttribute("aria-expanded", "true");
    el("review-loading").textContent = "Loading page previews and automatic findings…";
    el("retry-review").hidden = true;
    el("review-heading").focus({preventScroll:true});
    el("document-review").scrollIntoView({behavior:"instant",block:"start"});
    try {
      let pending;
      try { pending = JSON.parse(sessionStorage.getItem(repairStorageKey())); } catch {}
      if (pending?.body) {
        el("review-loading").textContent = "Reconnecting to your AI repair request…";
        decisions = pending.decisions || decisions;
        await runRepair(pending); return;
      }
      const data = await request(`/v1/documents/${encodeURIComponent(id)}/review`);
      if (ticket !== generation) return;
      if (!data.pages.length) throw new Error("No pages are available for review.");
      snapshot = data; decisions = {}; selected = null;
      el("review-layout-note").hidden = !data.regroup_recommended;
      el("review-layout-note").textContent = data.regroup_recommended ? "This output used the earlier layout. Run a fresh translation in Automatic or Improve readability to rebuild paragraphs. Rendering saved line translations alone keeps their existing regions." : "";
      try {
        const saved = JSON.parse(sessionStorage.getItem(storageKey()));
        if (saved?.version === data.version) for (const region of data.regions) {
          if (["okay", "change"].includes(saved.decisions?.[region.id])) decisions[region.id] = saved.decisions[region.id];
        }
      } catch { /* Review still works when storage is unavailable. */ }
      el("review-filename").textContent = `${data.filename}${data.output === "draft.pdf" ? " · Source-retaining draft" : ""}`;
      el("translated-page-heading").textContent = data.output === "draft.pdf" ? "Draft · source retained" : "Translated";
      el("review-page").replaceChildren();
      for (const page of data.pages) {
        const flagged = data.regions.filter(r => r.page_number === page.number && r.status !== "clear").length;
        const option = textNode("option", `${page.number}${flagged ? ` · ${flagged} flagged` : ""}`);
        option.value = page.number; el("review-page").append(option);
      }
      el("review-page-total").textContent = `of ${data.pages.length}`;
      const first = data.regions.find(r => matches(r, "flagged")) || data.regions.find(r => !decisions[r.id]) || data.regions[0];
      pageNumber = first?.page_number || data.pages[0].number;
      el("review-filter").value = data.regions.some(r => r.status !== "clear") ? "flagged" : "all";
      el("review-loading").textContent = ""; el("review-content").hidden = false;
      overview(); loadPage();
      if (first && matches(first, el("review-filter").value)) choose(first.id, false); else { regionList(); detail(); highlights(); }
    } catch (error) {
      if (ticket !== generation) return;
      el("review-loading").textContent = `Could not load the review. ${error.message} Your PDF download is still available.`;
      el("retry-review").hidden = false;
    }
  }
  el("repair-marked").addEventListener("click", () => {
    let pending;
    try { pending = JSON.parse(sessionStorage.getItem(repairStorageKey())); } catch {}
    runRepair(pending);
  });
  el("see-issues").addEventListener("click", open);
  el("retry-review").addEventListener("click", open);
  el("close-review").addEventListener("click", () => {
    generation++; repairRun++; repairing = false; el("document-review").hidden = true;
    el("see-issues").setAttribute("aria-expanded", "false"); el("see-issues").focus();
  });
  el("review-page").addEventListener("change", () => changePage(Number(el("review-page").value)));
  for (const [name, delta] of [["prev",-1],["next",1]]) el(`review-${name}`).addEventListener("click", () => {
    const index = snapshot.pages.findIndex(p => p.number === pageNumber);
    if (snapshot.pages[index + delta]) changePage(snapshot.pages[index + delta].number);
  });
  el("review-filter").addEventListener("change", refreshSelection);
  el("review-highlights").addEventListener("change", () => { if (snapshot) highlights(); });
  el("review-zoom").addEventListener("change", zoom);
  for (const [id, value] of [["review-okay","okay"],["review-change","change"],["review-reset",null]]) el(id).addEventListener("click", () => {
    if (!selected || repairing) return;
    if (value) decisions[selected] = value; else delete decisions[selected];
    overview(); saveDecisions(); refreshSelection();
  });
  for (const [from, to] of [["source","translated"],["translated","source"]]) el(`${from}-scroll`).addEventListener("scroll", () => {
    if (scrollLock || !el("review-sync").checked) return;
    scrollLock = true;
    const a = el(`${from}-scroll`), b = el(`${to}-scroll`);
    b.scrollTop = a.scrollTop / Math.max(1,a.scrollHeight-a.clientHeight) * (b.scrollHeight-b.clientHeight);
    b.scrollLeft = a.scrollLeft / Math.max(1,a.scrollWidth-a.clientWidth) * (b.scrollWidth-b.clientWidth);
    requestAnimationFrame(() => { scrollLock = false; });
  });
  window.documentReview = {offer, reset};
  // Recover a user-requested repair after a page reload without storing text or
  // image crops in the browser. The server retains the queued job and context.
  try {
    if (!active) {
      const key = Object.keys(sessionStorage).find(key => key.startsWith("translation-studio-repair:"));
      if (key) { offer(key.slice("translation-studio-repair:".length)); open(); }
    }
  } catch { /* Explicit reopening still works if browser storage is unavailable. */ }
})();
