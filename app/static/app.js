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
let loadedPageId = null;
let drawState = null;
let deletedBoxPatterns = [];

const $ = (id) => document.getElementById(id);

$("uploadBtn").addEventListener("click", uploadPdf);
$("prevBtn").addEventListener("click", () => changePage(-1));
$("nextBtn").addEventListener("click", () => changePage(1));
$("fitBtn").addEventListener("click", fitToView);
$("actualBtn").addEventListener("click", () => setZoom(1.0));
$("addBoxBtn").addEventListener("click", addBox);
$("redetectBtn").addEventListener("click", redetectSheet);
$("deleteBtn").addEventListener("click", deleteSelected);
$("approveBtn").addEventListener("click", approveSheet);
$("skipBtn").addEventListener("click", skipSheet);
$("downloadBtn").addEventListener("click", downloadProject);
$("refreshLibraryBtn").addEventListener("click", loadLibrary);
$("scanUnscannedBtn").addEventListener("click", scanUnscannedDetails);
$("gridToggleBtn").addEventListener("click", () => { libraryGrid = !libraryGrid; loadLibrary(); });
$("closeDetailBtn").addEventListener("click", () => $("detailModal").classList.add("hidden"));
$("closeNoteBtn").addEventListener("click", () => $("noteModal").classList.add("hidden"));
$("noteModal").addEventListener("click", (e) => { if (e.target === $("noteModal")) $("noteModal").classList.add("hidden"); });
for (const btn of document.querySelectorAll(".tab-btn")) {
  btn.addEventListener("click", () => showTab(btn.dataset.tab));
}

for (const id of ["librarySearch", "filterDesignTeam", "filterTag", "filterCsi", "filterBookmarked"]) {
  $(id).addEventListener("input", debounce(loadLibrary, 250));
  $(id).addEventListener("change", loadLibrary);
}
for (const id of ["filterProjects", "filterDisciplines"]) {
  $(id).addEventListener("change", loadLibrary);
}

const dropZone = $("dropZone");
const fileInput = $("pdfFile");
const canvasWrap = $("canvasWrap");

loadDesignTeams();
loadLibraryFacets();
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
canvasWrap.addEventListener("mousedown", startDrawBoxMaybe);

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

function showTab(tabId) {
  for (const panel of document.querySelectorAll(".tab-panel")) {
    panel.classList.toggle("hidden", panel.id !== tabId);
    panel.classList.toggle("active", panel.id === tabId);
  }
  for (const btn of document.querySelectorAll(".tab-btn")) {
    btn.classList.toggle("active", btn.dataset.tab === tabId);
  }
  if (tabId === "libraryTab") { loadLibraryFacets(); loadLibrary(); }
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
  await loadLibraryFacets();
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

  const current = manifest.pages.find(p => p.page_index === pageIndex) || manifest.pages[pageIndex];
  const imageHasLoadedPage = loadedPageId && $("sheetImage").getAttribute("src");
  if (current?.status === "ready" && current.image && (!imageHasLoadedPage || loadedPageId !== current.id)) {
    loadPage();
  } else if (!current || current.status !== "ready" || !current.image) {
    const ready = data.pages.find(p => p.status === "ready");
    if (ready) {
      pageIndex = ready.page_index;
      loadPage();
    } else {
      showProcessingNext(data.processing_status);
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
  if (!data.page) return showProcessingNext(data.processing_status);
  const idx = manifest.pages.findIndex(p => p.id === data.page.id);
  if (idx >= 0) manifest.pages[idx] = data.page;
  pageIndex = data.page.page_index;
  loadPage();
}

function showProcessingNext(status = manifest?.processing_status) {
  loadedPageId = null;
  $("sheetImage").removeAttribute("src");
  $("boxLayer").innerHTML = "";
  $("boxList").innerHTML = "";
  const pagesReady = status?.pages_ready || 0;
  const pagesProcessing = status?.pages_processing || 0;
  const remainingReviewable = pagesReady + pagesProcessing;
  if (manifest && remainingReviewable === 0) {
    $("sheetInfo").textContent = "All uploaded drawings have been reviewed.";
    $("detailsList").innerHTML = `<div class="done-state"><strong>Drawing review complete.</strong><br>${status?.pages_approved || 0} sheets approved, ${status?.pages_skipped || 0} sheets skipped, ${status?.pages_failed || 0} failed. AI tagging may continue in the background.</div>`;
    return;
  }
  $("sheetInfo").textContent = "Processing next sheet...";
  $("detailsList").textContent = "No ready sheets yet. Page rendering/detection is continuing in the background.";
}

function loadPage() {
  const page = manifest.pages.find(p => p.page_index === pageIndex) || manifest.pages[pageIndex];
  if (!page || page.status !== "ready" || !page.image) return showProcessingNext();
  pageIndex = page.page_index;
  boxes = applyDeletedBoxPatterns(structuredClone(page.boxes || []), page);
  selectedId = null;
  currentMergeTargetId = null;

  const img = $("sheetImage");
  loadedPageId = null;
  img.onload = () => {
    loadedPageId = page.id;
    $("boxLayer").style.width = `${img.naturalWidth}px`;
    $("boxLayer").style.height = `${img.naturalHeight}px`;
    $("sheetStage").style.width = `${img.naturalWidth}px`;
    $("sheetStage").style.height = `${img.naturalHeight}px`;
    fitToView();
    renderBoxes();
  };
  img.onerror = () => {
    loadedPageId = null;
    $("sheetInfo").textContent = "Sheet image is not ready yet. Retrying as processing continues...";
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


function imagePointFromClient(clientX, clientY, clamp = true) {
  const wrap = $("canvasWrap");
  const img = $("sheetImage");
  if (!img.naturalWidth || !img.naturalHeight) return null;
  const rect = wrap.getBoundingClientRect();
  const margin = stageMarginPx();
  const x = (clientX - rect.left + wrap.scrollLeft - margin) / zoom;
  const y = (clientY - rect.top + wrap.scrollTop - margin) / zoom;
  if (!clamp && (x < 0 || y < 0 || x > img.naturalWidth || y > img.naturalHeight)) return null;
  return {
    x: Math.max(0, Math.min(img.naturalWidth, x)),
    y: Math.max(0, Math.min(img.naturalHeight, y)),
  };
}

function startDrawBoxMaybe(e) {
  if (e.button !== 0) return;
  if (!loadedPageId || e.target.closest(".crop-box")) return;

  const start = imagePointFromClient(e.clientX, e.clientY, false);
  if (!start) return;
  e.preventDefault();

  const id = `user_${Date.now()}`;
  const box = { id, x: start.x, y: start.y, w: 1, h: 1, confidence: 1.0, source: "user" };
  boxes.push(box);
  selectedId = id;
  drawState = { id, startX: start.x, startY: start.y, moved: false };

  document.addEventListener("mousemove", onDrawBox);
  document.addEventListener("mouseup", stopDrawBox);
  renderBoxes();
  renderBoxList();
}

function onDrawBox(e) {
  if (!drawState) return;
  const point = imagePointFromClient(e.clientX, e.clientY);
  const box = boxes.find(b => b.id === drawState.id);
  if (!point || !box) return;

  const x0 = Math.min(drawState.startX, point.x);
  const y0 = Math.min(drawState.startY, point.y);
  const x1 = Math.max(drawState.startX, point.x);
  const y1 = Math.max(drawState.startY, point.y);
  box.x = x0;
  box.y = y0;
  box.w = Math.max(1, x1 - x0);
  box.h = Math.max(1, y1 - y0);
  drawState.moved = box.w >= 4 || box.h >= 4;
  renderBoxes();
  renderBoxList();
}

function stopDrawBox() {
  if (!drawState) return;
  const box = boxes.find(b => b.id === drawState.id);
  const minSize = 20;
  if (!box || !drawState.moved || box.w < minSize || box.h < minSize) {
    boxes = boxes.filter(b => b.id !== drawState.id);
    selectedId = null;
  } else {
    box.w = Math.max(minSize, box.w);
    box.h = Math.max(minSize, box.h);
    applyOverlapMerge(box.id);
  }
  drawState = null;
  document.removeEventListener("mousemove", onDrawBox);
  document.removeEventListener("mouseup", stopDrawBox);
  renderBoxes();
  renderBoxList();
}

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


function currentPage() {
  return manifest?.pages?.find(p => p.page_index === pageIndex) || manifest?.pages?.[pageIndex] || null;
}

function boxSignature(box, page) {
  const img = $("sheetImage");
  const width = page?.width || img.naturalWidth || 1;
  const height = page?.height || img.naturalHeight || 1;
  return {
    x: box.x / width,
    y: box.y / height,
    w: box.w / width,
    h: box.h / height,
  };
}

function rememberDeletedBox(box) {
  const page = currentPage();
  if (!page || !box) return;
  const sig = boxSignature(box, page);
  const alreadyKnown = deletedBoxPatterns.some(pattern => signaturesMatch(sig, pattern, 0.008, 0.015));
  if (!alreadyKnown) deletedBoxPatterns.push(sig);
}

function signaturesMatch(a, b, positionTolerance = 0.012, sizeTolerance = 0.02) {
  return Math.abs(a.x - b.x) <= positionTolerance &&
    Math.abs(a.y - b.y) <= positionTolerance &&
    Math.abs(a.w - b.w) <= sizeTolerance &&
    Math.abs(a.h - b.h) <= sizeTolerance;
}

function applyDeletedBoxPatterns(candidateBoxes, page) {
  if (!deletedBoxPatterns.length) return candidateBoxes;
  return candidateBoxes.filter(box => {
    if (box.source && !box.source.startsWith("detector")) return true;
    const sig = boxSignature(box, page);
    return !deletedBoxPatterns.some(pattern => signaturesMatch(sig, pattern));
  });
}

function addBox() {
  const wrap = $("canvasWrap");
  const margin = stageMarginPx();
  const id = `user_${Date.now()}`;
  boxes.push({ id, x: ((wrap.scrollLeft - margin) / zoom) + 80, y: ((wrap.scrollTop - margin) / zoom) + 80, w: 500, h: 350, confidence: 1.0, source: "user" });
  selectedId = id; renderBoxes(); renderBoxList();
}
function deleteSelected() {
  if (!selectedId) return;
  const deleted = boxes.find(b => b.id === selectedId);
  rememberDeletedBox(deleted);
  boxes = boxes.filter(b => b.id !== selectedId);
  selectedId = null;
  currentMergeTargetId = null;
  renderBoxes();
  renderBoxList();
}
function deleteSelected() { if (!selectedId) return; boxes = boxes.filter(b => b.id !== selectedId); selectedId = null; currentMergeTargetId = null; renderBoxes(); renderBoxList(); }

async function redetectSheet() {
  if (!projectId) return;
  const page = currentPage();
  if (!page || page.status !== "ready") {
    alert("Wait until a sheet is ready before re-detecting boxes.");
    return;
  }
  if (!confirm("Re-run automatic box detection for this sheet? This will replace the current unapproved boxes on the sheet.")) return;
  const btn = $("redetectBtn");
  btn.disabled = true;
  btn.textContent = "Detecting...";
  try {
    const res = await fetch("/api/redetect-sheet", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ project_id: projectId, page_id: page.id })
    });
    if (!res.ok) { alert(await res.text()); return; }
    const data = await res.json();
    boxes = applyDeletedBoxPatterns(structuredClone(data.boxes || []), page);
    page.boxes = structuredClone(boxes);
    selectedId = null;
    currentMergeTargetId = null;
    renderBoxes();
    renderBoxList();
    $("sheetInfo").textContent = `${manifest.project_name || "Unnamed Project"} — ${page.source_filename || "PDF"} page ${page.page_number} — batch page ${page.page_index + 1} of ${manifest.pages.length} — ${boxes.length} candidate boxes`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Re-detect Boxes";
  }
}

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
  await loadLibraryFacets();
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
  card.className = "detail-card" + (d.bookmarked ? " bookmarked" : "");
  const tags = (d.tags || []).map(t => `<span class="tag">${escapeHtml(t)}</span>`).join(" ");
  const hasNote = Boolean((d.notes || "").trim());
  const noteBadge = hasNote ? `<button class="note-badge" type="button" aria-label="View note" title="View note">📝</button>` : "";
  card.innerHTML = `
    <div class="card-thumb-wrap"><img src="/data/projects/${d.project_id}/${d.thumbnail || d.crop_image}" alt="Detail thumbnail" /><button class="bookmark-badge" type="button" aria-label="Toggle bookmark">${d.bookmarked ? "★" : "☆"}</button>${noteBadge}</div>
    <strong>${escapeHtml(d.detail_title || "Untitled detail")}</strong><br>
    <small>${escapeHtml(d.project_name || "Unnamed project")}</small><br>
    <small>${escapeHtml(d.discipline || "unknown")}</small>
  `;
  card.querySelector(".bookmark-badge").addEventListener("click", async (e) => {
    e.stopPropagation();
    await toggleBookmark(d);
  });
  const noteBtn = card.querySelector(".note-badge");
  if (noteBtn) noteBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    showNotePopup(d);
  });
  card.addEventListener("click", () => openDetail(d.id));
  return card;
}

function showNotePopup(d) {
  $("noteModalTitle").textContent = d.detail_title || "Untitled detail";
  $("noteModalSubtitle").textContent = [d.project_name || "Unnamed project", d.discipline || ""].filter(Boolean).join(" — ");
  $("noteModalBody").textContent = d.notes || "";
  $("noteModal").classList.remove("hidden");
}

let scanPollTimer = null;

function setScanStatus(text) {
  const span = $("scanStatus");
  span.textContent = text || "";
  span.classList.toggle("hidden", !text);
}

async function scanUnscannedDetails() {
  const btn = $("scanUnscannedBtn");
  btn.disabled = true;
  try {
    const res = await fetch("/api/library/scan-unscanned", { method: "POST" });
    if (!res.ok) { alert(await res.text()); return; }
    const data = await res.json();
    if (!data.queued && !data.status.active) {
      setScanStatus("All details have already been scanned.");
      setTimeout(() => { if (!scanPollTimer) setScanStatus(""); }, 5000);
      return;
    }
    setScanStatus(`Queued ${data.queued} detail(s). AI scanning is running in the background...`);
    startScanPolling();
  } finally {
    btn.disabled = false;
  }
}

function startScanPolling() {
  if (scanPollTimer) clearInterval(scanPollTimer);
  scanPollTimer = setInterval(async () => {
    const res = await fetch("/api/library/scan-status");
    if (!res.ok) return;
    const s = await res.json();
    if (s.active > 0) {
      setScanStatus(`AI scanning in background: ${s.active} job(s) remaining...`);
    } else {
      clearInterval(scanPollTimer);
      scanPollTimer = null;
      setScanStatus("Background AI scan finished. Library refreshed.");
      setTimeout(() => { if (!scanPollTimer) setScanStatus(""); }, 6000);
      await loadLibraryFacets();
      await loadLibrary();
    }
  }, 2000);
}

async function toggleBookmark(detail) {
  const res = await fetch(`/api/details/${detail.id}`, {
    method: "PUT",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({bookmarked: !detail.bookmarked})
  });
  if (!res.ok) { alert(await res.text()); return; }
  await loadLibraryFacets();
  await loadLibrary();
  if (projectId) await loadDetails();
}

function selectedCheckboxValues(containerId) {
  return Array.from($(containerId).querySelectorAll("input[type=checkbox]:checked")).map(input => input.value);
}

async function loadLibraryFacets() {
  const res = await fetch("/api/library/facets");
  if (!res.ok) return;
  const facets = await res.json();
  renderCheckboxOptions(
    "filterProjects",
    (facets.projects || []).map(project => ({ value: project.id, label: project.project_name || "Unnamed project" })),
    "No projects yet"
  );
  const standardDisciplines = ["architectural", "structural", "civil", "mechanical", "electrical", "plumbing", "fire protection", "technology/security", "unknown"];
  const facetDisciplines = (facets.disciplines || []).map(item => typeof item === "string" ? item : item.discipline).filter(Boolean);
  const disciplines = Array.from(new Set([...standardDisciplines, ...facetDisciplines]));
  renderCheckboxOptions("filterDisciplines", disciplines.map(value => ({ value, label: value })), "No disciplines yet");
}

function renderCheckboxOptions(containerId, options, emptyLabel) {
  const container = $(containerId);
  const selected = new Set(selectedCheckboxValues(containerId));
  container.innerHTML = "";
  if (!options.length) {
    container.innerHTML = `<span class="empty-filter">${escapeHtml(emptyLabel)}</span>`;
    return;
  }
  for (const option of options) {
    const id = `${containerId}_${String(option.value).replace(/[^a-z0-9_-]/gi, "_")}`;
    const label = document.createElement("label");
    label.innerHTML = `<input id="${escapeAttr(id)}" type="checkbox" value="${escapeAttr(option.value)}" ${selected.has(String(option.value)) ? "checked" : ""} /> ${escapeHtml(option.label)}`;
    container.appendChild(label);
  }
}

async function loadLibrary() {
  const params = new URLSearchParams({
    q: $("librarySearch").value || "",
    project_ids: selectedCheckboxValues("filterProjects").join(","),
    design_team: $("filterDesignTeam").value || "",
    disciplines: selectedCheckboxValues("filterDisciplines").join(","),
    tag: $("filterTag").value || "",
    csi: $("filterCsi").value || "",
    bookmarked: $("filterBookmarked").checked ? "1" : ""
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
  $("detailModal").classList.remove("hidden");
  renderDetailEditor(d);
}

function renderDetailEditor(d) {
  $("detailView").innerHTML = `
    <div class="detail-editor">
      <div class="detail-preview">
        <div class="viewer-toolbar">
          <div class="detail-meta-line">
            <strong>${escapeHtml(d.project_name || "Unnamed project")}</strong>
            <span>${escapeHtml(d.design_team || "No design team")}</span>
            <span>${escapeHtml(d.source_filename || "PDF")}, page ${d.page_number || "?"}</span>
            <span>AI: ${escapeHtml(d.ai_status || "pending")}</span>
          </div>
          <div class="buttons">
            <button type="button" id="detailFitBtn">Fit</button>
            <button type="button" id="detailActualBtn">100%</button>
          </div>
        </div>
        <div id="detailViewerWrap" class="detail-viewer">
          <div id="detailViewerStage" class="detail-viewer-stage">
            <img id="detailViewerImage" src="/data/projects/${d.project_id}/${d.crop_image}" alt="Approved detail crop" draggable="false" />
          </div>
        </div>
        <p class="hint viewer-hint">Wheel = zoom. Drag (left or right button) = pan.</p>
      </div>
      <form id="detailEditForm" class="detail-form">
        <div class="form-header">
          <h2>Edit Detail</h2>
          <label class="bookmark-toggle"><input id="editBookmarked" type="checkbox" ${d.bookmarked ? "checked" : ""}/> ★ Bookmark</label>
        </div>
        <div class="form-grid-2">
          <label>Project Name<input id="editProjectName" type="text" value="${escapeAttr(d.project_name || "")}" /></label>
          <label>Design Team<input id="editDesignTeam" type="text" value="${escapeAttr(d.design_team || "")}" /></label>
        </div>
        <label>Detail Name<input id="editTitle" type="text" value="${escapeAttr(d.detail_title || "")}" placeholder="Untitled detail" /></label>
        <div class="form-grid-2">
          <label>Detail #<input id="editDetailNumber" type="text" value="${escapeAttr(d.detail_number || "")}" /></label>
          <label>Sheet #<input id="editSheetNumber" type="text" value="${escapeAttr(d.sheet_number || "")}" /></label>
        </div>
        <label>Discipline
          <select id="editDiscipline">
            ${disciplineOptions(d.discipline || "unknown")}
          </select>
        </label>
        <label>Tags<input id="editTags" type="text" value="${escapeAttr((d.tags || []).join(", "))}" placeholder="comma, separated, tags" /></label>
        <label>CSI Divisions<input id="editCsi" type="text" value="${escapeAttr((d.csi_divisions || []).join(", "))}" placeholder="comma, separated CSI divisions" /></label>
        <label>Notes<textarea id="editNotes" rows="4" placeholder="Personal notes, e.g. this was a pain to build...">${escapeHtml(d.notes || "")}</textarea></label>
        <label>AI Summary / Description<textarea id="editSummary" rows="5">${escapeHtml(d.summary || "")}</textarea></label>
        <label>Searchable Description<textarea id="editDescription" rows="5">${escapeHtml(d.searchable_description || "")}</textarea></label>
        <label>Assembly/System Type<input id="editAssembly" type="text" value="${escapeAttr(d.assembly_system_type || "")}" /></label>
        <div class="editor-actions">
          <button type="submit" class="primary-btn">Save Changes</button>
          <button type="button" id="rescanDetailBtn">AI Rescan</button>
          <button type="button" id="deleteDetailBtn" class="danger-btn">Delete Detail</button>
        </div>
        <p class="hint">Warnings: ${escapeHtml((d.warnings || []).join("; ") || "None")}</p>
      </form>
    </div>`;

  $("detailEditForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const updated = await saveDetailEdits(d.id);
    if (updated) renderDetailEditor(updated);
  });
  $("deleteDetailBtn").addEventListener("click", async () => deleteDetailFromEditor(d.id));
  $("rescanDetailBtn").addEventListener("click", async () => rescanDetailFromEditor(d));
  setupDetailViewer();
}

let detailZoom = 1.0;

function detailViewerStageMargin() {
  const s = window.getComputedStyle($("detailViewerStage"));
  return parseFloat(s.marginLeft || "0") || 0;
}

function setDetailZoom(z, anchorClientX = null, anchorClientY = null) {
  const wrap = $("detailViewerWrap");
  const stage = $("detailViewerStage");
  const img = $("detailViewerImage");
  if (!wrap || !img || !img.naturalWidth) return;
  const oldZoom = detailZoom;
  detailZoom = Math.max(0.05, Math.min(8.0, z));
  const margin = detailViewerStageMargin();
  const apply = () => {
    stage.style.transform = `scale(${detailZoom})`;
    stage.style.width = `${img.naturalWidth * detailZoom}px`;
    stage.style.height = `${img.naturalHeight * detailZoom}px`;
  };
  if (anchorClientX !== null && anchorClientY !== null) {
    const rect = wrap.getBoundingClientRect();
    const imageX = (anchorClientX - rect.left + wrap.scrollLeft - margin) / oldZoom;
    const imageY = (anchorClientY - rect.top + wrap.scrollTop - margin) / oldZoom;
    apply();
    wrap.scrollLeft = imageX * detailZoom + margin - (anchorClientX - rect.left);
    wrap.scrollTop = imageY * detailZoom + margin - (anchorClientY - rect.top);
  } else {
    apply();
  }
}

function fitDetailToView() {
  const wrap = $("detailViewerWrap");
  const img = $("detailViewerImage");
  if (!wrap || !img || !img.naturalWidth) return;
  const fitX = (wrap.clientWidth - 30) / img.naturalWidth;
  const fitY = (wrap.clientHeight - 30) / img.naturalHeight;
  setDetailZoom(Math.min(fitX, fitY));
  const margin = detailViewerStageMargin();
  wrap.scrollLeft = Math.max(0, margin - (wrap.clientWidth - img.naturalWidth * detailZoom) / 2);
  wrap.scrollTop = Math.max(0, margin - (wrap.clientHeight - img.naturalHeight * detailZoom) / 2);
}

function setupDetailViewer() {
  const wrap = $("detailViewerWrap");
  const img = $("detailViewerImage");
  if (!wrap || !img) return;

  if (img.complete && img.naturalWidth) fitDetailToView();
  else img.onload = fitDetailToView;

  $("detailFitBtn").addEventListener("click", fitDetailToView);
  $("detailActualBtn").addEventListener("click", () => setDetailZoom(1.0));
  wrap.addEventListener("wheel", (e) => {
    e.preventDefault();
    setDetailZoom(detailZoom * (e.deltaY > 0 ? 0.88 : 1.12), e.clientX, e.clientY);
  }, { passive: false });
  wrap.addEventListener("contextmenu", (e) => e.preventDefault());
  wrap.addEventListener("mousedown", (e) => {
    if (e.button !== 0 && e.button !== 2) return;
    e.preventDefault();
    const start = { x: e.clientX, y: e.clientY, left: wrap.scrollLeft, top: wrap.scrollTop };
    const onMove = (ev) => {
      wrap.scrollLeft = start.left - (ev.clientX - start.x);
      wrap.scrollTop = start.top - (ev.clientY - start.y);
    };
    const onUp = () => {
      wrap.classList.remove("panning");
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
    wrap.classList.add("panning");
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

function disciplineOptions(selected) {
  const values = ["architectural", "structural", "civil", "mechanical", "electrical", "plumbing", "fire protection", "technology/security", "unknown"];
  return values.map(value => `<option value="${escapeAttr(value)}" ${value === selected ? "selected" : ""}>${escapeHtml(value)}</option>`).join("");
}

async function saveDetailEdits(id) {
  const payload = {
    project_name: $("editProjectName").value.trim(),
    design_team: $("editDesignTeam").value.trim(),
    detail_title: $("editTitle").value.trim() || null,
    detail_number: $("editDetailNumber").value.trim() || null,
    sheet_number: $("editSheetNumber").value.trim() || null,
    discipline: $("editDiscipline").value,
    tags: $("editTags").value.split(",").map(t => t.trim()).filter(Boolean),
    csi_divisions: $("editCsi").value.split(",").map(t => t.trim()).filter(Boolean),
    summary: $("editSummary").value.trim() || null,
    searchable_description: $("editDescription").value.trim() || null,
    assembly_system_type: $("editAssembly").value.trim() || null,
    notes: $("editNotes").value.trim() || null,
    bookmarked: $("editBookmarked").checked,
  };
  const res = await fetch(`/api/details/${id}`, { method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload) });
  if (!res.ok) { alert(await res.text()); return null; }
  const updated = await res.json();
  await loadLibraryFacets();
  await loadLibraryFacets();
  await loadLibrary();
  if (projectId) await loadDetails();
  return updated;
}


async function rescanDetailFromEditor(detail) {
  const btn = $("rescanDetailBtn");
  btn.disabled = true;
  btn.textContent = "Rescanning...";
  try {
    const res = await fetch(`/api/details/${detail.id}/rescan`, { method: "POST" });
    if (!res.ok) { alert(await res.text()); return; }
    const data = await res.json();
    const proposal = data.proposal;
    if (confirm(aiProposalMessage(proposal))) {
      const updated = await applyAiProposal(detail.id, proposal);
      if (updated) renderDetailEditor(updated);
    }
  } finally {
    btn.disabled = false;
    btn.textContent = "AI Rescan";
  }
}

function aiProposalMessage(proposal) {
  return `AI rescan complete. Replace this detail's editable AI/catalog fields with the new result?\n\n` +
    `Title: ${proposal.detail_title || "(blank)"}\n` +
    `Detail #: ${proposal.detail_number || "(blank)"}\n` +
    `Sheet #: ${proposal.sheet_number || "(blank)"}\n` +
    `Discipline: ${proposal.discipline || "unknown"}\n` +
    `Tags: ${(proposal.tags || []).join(", ") || "(none)"}\n\n` +
    `Summary:\n${proposal.summary || "(blank)"}\n\n` +
    `Choose OK to replace, or Cancel to keep the current info.`;
}

async function applyAiProposal(id, proposal) {
  const payload = {
    detail_title: proposal.detail_title || null,
    detail_number: proposal.detail_number || null,
    sheet_number: proposal.sheet_number || null,
    discipline: proposal.discipline || "unknown",
    tags: proposal.tags || [],
    csi_divisions: proposal.csi_divisions || [],
    warnings: proposal.warnings || [],
    summary: proposal.summary || null,
    searchable_description: proposal.searchable_description || null,
    assembly_system_type: proposal.assembly_system_type || null,
    confidence_score: proposal.confidence_score ?? null,
  };
  const res = await fetch(`/api/details/${id}`, { method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload) });
  if (!res.ok) { alert(await res.text()); return null; }
  const updated = await res.json();
  await loadLibrary();
  if (projectId) await loadDetails();
  return updated;
}

async function deleteDetailFromEditor(id) {
  if (!confirm("Delete this detail crop and metadata? This cannot be undone.")) return;
  const res = await fetch(`/api/details/${id}`, { method: "DELETE" });
  if (!res.ok) { alert(await res.text()); return; }
  $("detailModal").classList.add("hidden");
  await loadLibrary();
  if (projectId) await loadDetails();
}


function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;","\"":"&quot;"}[c]));
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, "&#96;");
}

function downloadProject() { if (projectId) window.location.href = `/api/projects/${projectId}/download`; }
