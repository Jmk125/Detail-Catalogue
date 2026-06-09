let manifest = null;
let projectId = null;
let pageIndex = 0;
let boxes = [];
let selectedId = null;
let dragState = null;
let panState = null;
let zoom = 1.0;
let currentMergeTargetId = null;
let pollTimer = null;
let libraryGrid = true;

const $ = (id) => document.getElementById(id);

$("uploadBtn").addEventListener("click", uploadPdf);
$("prevBtn").addEventListener("click", () => changePage(-1));
$("nextBtn").addEventListener("click", () => changePage(1));
$("fitBtn").addEventListener("click", fitToView);
$("actualBtn").addEventListener("click", () => setZoom(1.0));
$("addBoxBtn").addEventListener("click", addBox);
$("deleteBtn").addEventListener("click", deleteSelected);
$("approveBtn").addEventListener("click", approveSheet);
$("skipBtn").addEventListener("click", skipSheet);
$("downloadBtn").addEventListener("click", downloadProject);
$("refreshLibraryBtn").addEventListener("click", loadLibrary);
$("gridToggleBtn").addEventListener("click", () => { libraryGrid = !libraryGrid; loadLibrary(); });
$("closeDetailBtn").addEventListener("click", () => $("detailModal").classList.add("hidden"));

for (const id of ["librarySearch", "filterProject", "filterDesignTeam", "filterDiscipline", "filterTag", "filterCsi"]) {
  $(id).addEventListener("input", debounce(loadLibrary, 250));
  $(id).addEventListener("change", loadLibrary);
}

const dropZone = $("dropZone");
const fileInput = $("pdfFile");
const canvasWrap = $("canvasWrap");

loadDesignTeams();
loadLibrary();

dropZone.addEventListener("click", () => fileInput.click());
dropZone.addEventListener("dragover", (e) => { e.preventDefault(); dropZone.classList.add("dragover"); });
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropZone.classList.remove("dragover");
  if (e.dataTransfer.files.length) {
    fileInput.files = e.dataTransfer.files;
    setSelectedFileStatus();
  }
});
fileInput.addEventListener("change", setSelectedFileStatus);

canvasWrap.addEventListener("wheel", onWheelZoom, { passive: false });
canvasWrap.addEventListener("contextmenu", (e) => e.preventDefault());
canvasWrap.addEventListener("mousedown", startPanMaybe);

document.addEventListener("keydown", (e) => {
  if (e.key === "Delete" && selectedId) {
    e.preventDefault();
    deleteSelected();
  }
});

function setSelectedFileStatus() {
  const names = Array.from(fileInput.files || []).map(f => f.name);
  if (names.length) $("status").textContent = `Selected ${names.length} PDF(s): ${names.join(", ")}`;
}

function debounce(fn, delay) {
  let timer;
  return () => { clearTimeout(timer); timer = setTimeout(fn, delay); };
}

async function loadDesignTeams() {
  const res = await fetch("/api/design-teams");
  if (!res.ok) return;
  const data = await res.json();
  const list = $("designTeamOptions");
  list.innerHTML = "";
  for (const team of data.design_teams) {
    const option = document.createElement("option");
    option.value = team.name;
    list.appendChild(option);
  }
}

async function uploadPdf() {
  const files = Array.from(fileInput.files || []);
  if (!files.length) {
    alert("Choose one or more PDFs first.");
    return;
  }

  $("status").textContent = "Uploading PDFs and starting background page detection...";

  const form = new FormData();
  for (const file of files) form.append("files", file);
  form.append("project_name", $("projectName").value || "");
  form.append("design_team", $("designTeam").value || "");
  form.append("discipline", $("discipline").value || "unknown");

  const res = await fetch("/api/projects", { method: "POST", body: form });
  if (!res.ok) {
    $("status").textContent = "Upload failed.";
    alert(await res.text());
    return;
  }

  manifest = await res.json();
  projectId = manifest.project_id;
  pageIndex = 0;
  $("reviewPanel").classList.remove("hidden");
  $("status").textContent = `Project created with ${manifest.pages.length} pages. Waiting for first ready sheet...`;
  renderProcessingStatus(manifest.processing_status);
  startPolling();
  await loadNextReady(-1);
  await loadDesignTeams();
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(refreshProjectStatus, 1500);
}

async function refreshProjectStatus() {
  if (!projectId) return;
  const res = await fetch(`/api/projects/${projectId}/status`);
  if (!res.ok) return;
  const data = await res.json();
  manifest.pages = data.pages;
  manifest.processing_status = data.processing_status;
  renderProcessingStatus(data.processing_status);

  const current = manifest.pages[pageIndex];
  if (!current || current.status !== "ready" || !current.image) {
    const ready = data.pages.find(p => p.status === "ready");
    if (ready) {
      pageIndex = ready.page_index;
      loadPage();
    } else {
      showProcessingNext();
    }
  }
  if (data.processing_status.pages_processing === 0 && data.processing_status.ai_jobs.pending === 0 && data.processing_status.ai_jobs.running === 0) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function renderProcessingStatus(status) {
  const div = $("processingStatus");
  div.classList.remove("hidden");
  const ai = status?.ai_jobs || {};
  div.innerHTML = `
    <span>Ready: <strong>${status?.pages_ready || 0}</strong></span>
    <span>Processing: <strong>${status?.pages_processing || 0}</strong></span>
    <span>Approved: <strong>${status?.pages_approved || 0}</strong></span>
    <span>Skipped: <strong>${status?.pages_skipped || 0}</strong></span>
    <span>AI pending/running/complete/failed: <strong>${ai.pending || 0}/${ai.running || 0}/${ai.complete || 0}/${ai.failed || 0}</strong></span>
  `;
}

async function loadNextReady(afterIndex) {
  const res = await fetch(`/api/projects/${projectId}/next-ready?after_index=${afterIndex}`);
  if (!res.ok) return showProcessingNext();
  const data = await res.json();
  renderProcessingStatus(data.processing_status);
  if (!data.page) return showProcessingNext();
  const idx = manifest.pages.findIndex(p => p.id === data.page.id);
  if (idx >= 0) manifest.pages[idx] = data.page;
  pageIndex = data.page.page_index;
  loadPage();
}

function showProcessingNext() {
  $("sheetInfo").textContent = "Processing next sheet...";
  $("sheetImage").removeAttribute("src");
  $("boxLayer").innerHTML = "";
  $("boxList").innerHTML = "";
  $("detailsList").textContent = "No ready sheets yet. Page rendering/detection is continuing in the background.";
}

function loadPage() {
  const page = manifest.pages.find(p => p.page_index === pageIndex) || manifest.pages[pageIndex];
  if (!page || page.status !== "ready" || !page.image) return showProcessingNext();
  pageIndex = page.page_index;
  boxes = structuredClone(page.boxes || []);
  selectedId = null;
  currentMergeTargetId = null;

  const img = $("sheetImage");
  img.onload = () => {
    $("boxLayer").style.width = `${img.naturalWidth}px`;
    $("boxLayer").style.height = `${img.naturalHeight}px`;
    $("sheetStage").style.width = `${img.naturalWidth}px`;
    $("sheetStage").style.height = `${img.naturalHeight}px`;
    fitToView();
    renderBoxes();
  };

  img.src = `/data/projects/${projectId}/${page.image}?t=${Date.now()}`;
  $("sheetInfo").textContent = `${manifest.project_name || "Unnamed Project"} — ${page.source_filename || "PDF"} page ${page.page_number} — batch page ${page.page_index + 1} of ${manifest.pages.length} — ${boxes.length} candidate boxes`;
  renderBoxList();
  loadDetails();
}

function changePage(delta) {
  if (!manifest) return;
  const readyPages = manifest.pages.filter(p => p.status === "ready").sort((a, b) => a.page_index - b.page_index);
  if (!readyPages.length) return showProcessingNext();
  const currentPos = readyPages.findIndex(p => p.page_index === pageIndex);
  const nextPos = Math.max(0, Math.min(readyPages.length - 1, (currentPos >= 0 ? currentPos : 0) + delta));
  pageIndex = readyPages[nextPos].page_index;
  loadPage();
}

function stageMarginPx() {
  const s = window.getComputedStyle($("sheetStage"));
  return parseFloat(s.marginLeft || "0") || 0;
}

function setZoom(z, anchorClientX = null, anchorClientY = null) {
  const wrap = $("canvasWrap");
  const oldZoom = zoom;
  zoom = Math.max(0.08, Math.min(3.0, z));

  if (anchorClientX !== null && anchorClientY !== null) {
    const rect = wrap.getBoundingClientRect();
    const margin = stageMarginPx();
    const mouseX = anchorClientX - rect.left + wrap.scrollLeft - margin;
    const mouseY = anchorClientY - rect.top + wrap.scrollTop - margin;
    const imageX = mouseX / oldZoom;
    const imageY = mouseY / oldZoom;
    applyZoom();
    wrap.scrollLeft = imageX * zoom + margin - (anchorClientX - rect.left);
    wrap.scrollTop = imageY * zoom + margin - (anchorClientY - rect.top);
  } else {
    applyZoom();
  }
  renderBoxes();
}

function applyZoom() {
  const stage = $("sheetStage");
  const img = $("sheetImage");
  stage.style.transform = `scale(${zoom})`;
  stage.style.width = `${img.naturalWidth * zoom}px`;
  stage.style.height = `${img.naturalHeight * zoom}px`;
}

function fitToView() {
  const wrap = $("canvasWrap");
  const img = $("sheetImage");
  if (!img.naturalWidth) return;
  const fitX = (wrap.clientWidth - 30) / img.naturalWidth;
  const fitY = (wrap.clientHeight - 30) / img.naturalHeight;
  setZoom(Math.min(fitX, fitY, 0.65));
  const margin = stageMarginPx();
  wrap.scrollLeft = margin;
  wrap.scrollTop = margin;
}

function onWheelZoom(e) { e.preventDefault(); setZoom(zoom * (e.deltaY > 0 ? 0.88 : 1.12), e.clientX, e.clientY); }

function startPanMaybe(e) {
  if (e.button !== 2) return;
  e.preventDefault();
  panState = { startX: e.clientX, startY: e.clientY, scrollLeft: canvasWrap.scrollLeft, scrollTop: canvasWrap.scrollTop };
  canvasWrap.classList.add("panning");
  document.addEventListener("mousemove", onPan);
  document.addEventListener("mouseup", stopPan);
}
function onPan(e) { if (!panState) return; canvasWrap.scrollLeft = panState.scrollLeft - (e.clientX - panState.startX); canvasWrap.scrollTop = panState.scrollTop - (e.clientY - panState.startY); }
function stopPan() { panState = null; canvasWrap.classList.remove("panning"); document.removeEventListener("mousemove", onPan); document.removeEventListener("mouseup", stopPan); }

function getResizeModeFromMouseEvent(e, boxElement) {
  const rect = boxElement.getBoundingClientRect();
  const x = e.clientX - rect.left;
  const y = e.clientY - rect.top;
  const edgePx = 10;
  let mode = "";
  if (y <= edgePx) mode += "n";
  if (y >= rect.height - edgePx) mode += "s";
  if (x <= edgePx) mode += "w";
  if (x >= rect.width - edgePx) mode += "e";
  return mode || "move";
}
function cursorForMode(mode) {
  if (mode === "move") return "move";
  if (mode === "n" || mode === "s") return "ns-resize";
  if (mode === "e" || mode === "w") return "ew-resize";
  if (mode === "nw" || mode === "se") return "nwse-resize";
  if (mode === "ne" || mode === "sw") return "nesw-resize";
  return "move";
}

function renderBoxes() {
  const layer = $("boxLayer");
  layer.innerHTML = "";
  const borderPx = Math.max(1, 3 / zoom);
  for (const box of boxes) {
    const el = document.createElement("div");
    el.className = "crop-box" + (box.id === selectedId ? " selected" : "") + (box.id === currentMergeTargetId ? " merge-target" : "");
    el.style.left = `${box.x}px`; el.style.top = `${box.y}px`; el.style.width = `${box.w}px`; el.style.height = `${box.h}px`;
    el.style.borderWidth = `${borderPx}px`; el.dataset.id = box.id; el.style.cursor = "move";
    const label = document.createElement("div");
    label.className = "label"; label.textContent = box.id; label.style.fontSize = `${12 / zoom}px`; label.style.top = `${-22 / zoom}px`; label.style.padding = `${2 / zoom}px ${5 / zoom}px`;
    el.appendChild(label);
    el.addEventListener("mousemove", (e) => { if (!dragState) el.style.cursor = cursorForMode(getResizeModeFromMouseEvent(e, el)); });
    el.addEventListener("mousedown", (e) => { if (e.button !== 0) return; e.stopPropagation(); startDrag(e, box.id, getResizeModeFromMouseEvent(e, el)); });
    layer.appendChild(el);
  }
}

function renderBoxList() {
  const list = $("boxList");
  list.innerHTML = "";
  boxes.forEach((box, i) => {
    const li = document.createElement("li");
    li.textContent = `${i + 1}. ${Math.round(box.w)} × ${Math.round(box.h)} — ${box.source}`;
    li.className = box.id === selectedId ? "selected" : "";
    li.onclick = () => { selectedId = box.id; currentMergeTargetId = null; renderBoxes(); renderBoxList(); };
    list.appendChild(li);
  });
}

function startDrag(e, id, mode) {
  selectedId = id;
  const box = boxes.find(b => b.id === id);
  dragState = { mode, id, startX: e.clientX, startY: e.clientY, orig: { ...box }, moved: false };
  document.addEventListener("mousemove", onDrag);
  document.addEventListener("mouseup", stopDrag);
  renderBoxes(); renderBoxList();
}

function onDrag(e) {
  if (!dragState) return;
  const box = boxes.find(b => b.id === dragState.id);
  const dx = (e.clientX - dragState.startX) / zoom;
  const dy = (e.clientY - dragState.startY) / zoom;
  const o = dragState.orig;
  const mode = dragState.mode;
  const minSize = 30;
  if (Math.abs(dx) > 2 || Math.abs(dy) > 2) dragState.moved = true;
  let x = o.x, y = o.y, w = o.w, h = o.h;
  if (mode === "move") { x = o.x + dx; y = o.y + dy; }
  else {
    if (mode.includes("w")) { x = o.x + dx; w = o.w - dx; }
    if (mode.includes("e")) w = o.w + dx;
    if (mode.includes("n")) { y = o.y + dy; h = o.h - dy; }
    if (mode.includes("s")) h = o.h + dy;
    if (w < minSize) { if (mode.includes("w")) x = o.x + o.w - minSize; w = minSize; }
    if (h < minSize) { if (mode.includes("n")) y = o.y + o.h - minSize; h = minSize; }
  }
  box.x = Math.max(0, x); box.y = Math.max(0, y); box.w = Math.max(minSize, w); box.h = Math.max(minSize, h);
  currentMergeTargetId = findMergeTarget(box.id);
  renderBoxes(); renderBoxList();
}

function stopDrag() {
  if (dragState && dragState.moved) applyOverlapMerge(dragState.id);
  dragState = null; currentMergeTargetId = null;
  document.removeEventListener("mousemove", onDrag);
  document.removeEventListener("mouseup", stopDrag);
  renderBoxes(); renderBoxList();
}

function boxArea(b) { return b.w * b.h; }
function intersectionArea(a, b) { const x0 = Math.max(a.x, b.x); const y0 = Math.max(a.y, b.y); const x1 = Math.min(a.x + a.w, b.x + b.w); const y1 = Math.min(a.y + a.h, b.y + b.h); return Math.max(0, x1 - x0) * Math.max(0, y1 - y0); }
function unionBox(a, b) { const x0 = Math.min(a.x, b.x); const y0 = Math.min(a.y, b.y); const x1 = Math.max(a.x + a.w, b.x + b.w); const y1 = Math.max(a.y + a.h, b.y + b.h); return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }; }
function findMergeTarget(activeId) {
  const active = boxes.find(b => b.id === activeId);
  if (!active) return null;
  let best = null, bestRatio = 0;
  for (const other of boxes) {
    if (other.id === activeId) continue;
    const ratio = intersectionArea(active, other) / Math.max(1, Math.min(boxArea(active), boxArea(other)));
    if (ratio >= 0.50 && ratio > bestRatio) { best = other; bestRatio = ratio; }
  }
  return best ? best.id : null;
}
function applyOverlapMerge(activeId) {
  const active = boxes.find(b => b.id === activeId);
  const targetId = findMergeTarget(activeId);
  if (!active || !targetId) return;
  const target = boxes.find(b => b.id === targetId);
  if (!target) return;
  const activeIsSmaller = boxArea(active) <= boxArea(target);
  const keep = activeIsSmaller ? target : active;
  const remove = activeIsSmaller ? active : target;
  const u = unionBox(active, target);
  keep.x = u.x; keep.y = u.y; keep.w = u.w; keep.h = u.h;
  keep.source = keep.source.includes("merged") ? keep.source : `${keep.source}+merged`;
  boxes = boxes.filter(b => b.id !== remove.id);
  selectedId = keep.id;
}

function addBox() {
  const wrap = $("canvasWrap");
  const margin = stageMarginPx();
  const id = `user_${Date.now()}`;
  boxes.push({ id, x: ((wrap.scrollLeft - margin) / zoom) + 80, y: ((wrap.scrollTop - margin) / zoom) + 80, w: 500, h: 350, confidence: 1.0, source: "user" });
  selectedId = id; renderBoxes(); renderBoxList();
}
function deleteSelected() { if (!selectedId) return; boxes = boxes.filter(b => b.id !== selectedId); selectedId = null; currentMergeTargetId = null; renderBoxes(); renderBoxList(); }

async function approveSheet() {
  if (!projectId) return;
  const page = manifest.pages.find(p => p.page_index === pageIndex);
  if (!page) return;
  const res = await fetch("/api/approve-sheet", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ project_id: projectId, page_id: page.id, boxes })
  });
  if (!res.ok) { alert(await res.text()); return; }
  const data = await res.json();
  page.status = "approved"; page.boxes = structuredClone(boxes); page.approved = true;
  renderProcessingStatus(data.processing_status);
  loadDetails();
  loadLibrary();
  await loadNextReady(page.page_index);
}

async function skipSheet() {
  if (!projectId) return;
  const page = manifest.pages.find(p => p.page_index === pageIndex);
  if (!page) return;
  const res = await fetch("/api/skip-sheet", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ project_id: projectId, page_id: page.id }) });
  if (!res.ok) { alert(await res.text()); return; }
  const data = await res.json();
  page.status = "skipped";
  renderProcessingStatus(data.processing_status);
  await loadNextReady(page.page_index);
}

async function loadDetails() {
  if (!projectId) return;
  const res = await fetch(`/api/projects/${projectId}/details`);
  const data = await res.json();
  const div = $("detailsList");
  div.innerHTML = "";
  const page = manifest.pages.find(p => p.page_index === pageIndex);
  const pageDetails = data.details.filter(d => page && d.page_id === page.id);
  if (pageDetails.length === 0) { div.textContent = "No approved details saved for this sheet yet."; return; }
  for (const d of pageDetails) div.appendChild(detailCard(d, true));
}

function detailCard(d, compact = false) {
  const card = document.createElement("div");
  card.className = "detail-card";
  const tags = (d.tags || []).map(t => `<span class="tag">${escapeHtml(t)}</span>`).join(" ");
  card.innerHTML = `
    <img src="/data/projects/${d.project_id}/${d.thumbnail || d.crop_image}" alt="Detail thumbnail" />
    <strong>${escapeHtml(d.detail_title || "Untitled detail")}</strong><br>
    <small>${escapeHtml(d.project_name || "Unnamed project")} · ${escapeHtml(d.design_team || "No design team")} · ${escapeHtml(d.discipline || "unknown")}</small><br>
    <small>${escapeHtml(d.source_filename || "PDF")} page ${d.page_number || "?"} · Detail ${escapeHtml(d.detail_number || "?")} · Sheet ${escapeHtml(d.sheet_number || "?")}</small>
    <p>${escapeHtml(d.summary || "No AI summary yet.")}</p>
    <div>${tags}</div>
    <small>AI: ${escapeHtml(d.ai_status || "pending")}</small>
  `;
  card.addEventListener("click", () => openDetail(d.id));
  return card;
}

async function loadLibrary() {
  const params = new URLSearchParams({
    q: $("librarySearch").value || "",
    project: $("filterProject").value || "",
    design_team: $("filterDesignTeam").value || "",
    discipline: $("filterDiscipline").value || "",
    tag: $("filterTag").value || "",
    csi: $("filterCsi").value || ""
  });
  const res = await fetch(`/api/library/search?${params.toString()}`);
  if (!res.ok) return;
  const data = await res.json();
  const div = $("libraryResults");
  div.className = libraryGrid ? "library-grid" : "library-list";
  div.innerHTML = "";
  if (!data.details.length) { div.textContent = "No details found yet."; return; }
  for (const d of data.details) div.appendChild(detailCard(d));
}

async function openDetail(id) {
  const res = await fetch(`/api/details/${id}`);
  if (!res.ok) return;
  const d = await res.json();
  $("detailView").innerHTML = `
    <h2>${escapeHtml(d.detail_title || "Untitled detail")}</h2>
    <img class="detail-large" src="/data/projects/${d.project_id}/${d.crop_image}" alt="Approved detail crop" />
    <dl>
      <dt>Project</dt><dd>${escapeHtml(d.project_name || "")}</dd>
      <dt>Design Team</dt><dd>${escapeHtml(d.design_team || "")}</dd>
      <dt>Discipline</dt><dd>${escapeHtml(d.discipline || "unknown")}</dd>
      <dt>Source PDF</dt><dd>${escapeHtml(d.source_filename || "")}, page ${d.page_number || "?"}</dd>
      <dt>Detail / Sheet #</dt><dd>${escapeHtml(d.detail_number || "?")} / ${escapeHtml(d.sheet_number || "?")}</dd>
      <dt>CSI</dt><dd>${escapeHtml((d.csi_divisions || []).join(", "))}</dd>
      <dt>Tags</dt><dd>${escapeHtml((d.tags || []).join(", "))}</dd>
      <dt>Summary</dt><dd>${escapeHtml(d.summary || "")}</dd>
      <dt>Description</dt><dd>${escapeHtml(d.searchable_description || "")}</dd>
      <dt>Warnings</dt><dd>${escapeHtml((d.warnings || []).join("; "))}</dd>
    </dl>`;
  $("detailModal").classList.remove("hidden");
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;","\"":"&quot;"}[c]));
}

function downloadProject() { if (projectId) window.location.href = `/api/projects/${projectId}/download`; }
