let manifest = null;
let projectId = null;
let pageIndex = 0;
let boxes = [];
let selectedId = null;
let dragState = null;
let panState = null;
let zoom = 1.0;
let currentMergeTargetIds = [];
let pollTimer = null;
let libraryGrid = true;
let loadedPageId = null;
let drawState = null;
let deletedBoxPatterns = [];
let sheetBox = null;
let sheetDragState = null;
let compareMode = false;
const compareDetailIds = new Set();
const compareDetails = new Map();

const $ = (id) => document.getElementById(id);

const DISCIPLINE_VALUES = ["architectural", "structural", "civil", "mechanical", "electrical", "plumbing", "fire protection", "technology/security", "unknown"];

const ENGINEER_DISCIPLINES = ["structural", "civil", "mechanical", "plumbing", "electrical", "technology/security"];
const ENGINEER_LABELS = {
  structural: "Structural Engineer",
  civil: "Civil Engineer",
  mechanical: "Mechanical Engineer",
  plumbing: "Plumbing Engineer",
  electrical: "Electrical Engineer",
  "technology/security": "Technology Engineer",
};

function renderEngineerFields(containerId, designers = []) {
  $(containerId).innerHTML = ENGINEER_DISCIPLINES.map(discipline => {
    const existing = designers.find(d => d.discipline === discipline);
    return `<label>${ENGINEER_LABELS[discipline]}<input type="text" class="designer-firm-fixed" data-discipline="${discipline}" list="firmNameOptions" placeholder="Optional" value="${escapeAttr(existing?.firm_name || "")}" /></label>`;
  }).join("");
}

function collectDesigners(containerId = "designerRows") {
  const inputs = $(containerId).querySelectorAll(".designer-firm-fixed");
  const designers = [];
  for (const input of inputs) {
    const firmName = input.value.trim();
    if (firmName) designers.push({ discipline: input.dataset.discipline, firm_name: firmName });
  }
  return designers;
}

renderEngineerFields("designerRows");

$("prevBtn").addEventListener("click", () => changePage(-1));
$("fitBtn").addEventListener("click", fitToView);
$("actualBtn").addEventListener("click", () => setZoom(1.0));
$("addBoxBtn").addEventListener("click", addBox);
$("redetectBtn").addEventListener("click", redetectSheet);
$("deleteBtn").addEventListener("click", deleteSelected);
$("approveBtn").addEventListener("click", approveSheet);
$("skipBtn").addEventListener("click", skipSheet);
$("downloadBtn").addEventListener("click", downloadProject);
$("refreshLibraryBtn").addEventListener("click", loadLibrary);
$("compareModeBtn").addEventListener("click", toggleCompareMode);
$("goToLibraryCompareBtn").addEventListener("click", () => enableCompareMode({ switchToLibrary: true }));
$("clearCompareBtn").addEventListener("click", clearComparison);
$("scanUnscannedBtn").addEventListener("click", scanUnscannedDetails);
$("gridToggleBtn").addEventListener("click", () => { libraryGrid = !libraryGrid; loadLibrary(); });
$("closeDetailBtn").addEventListener("click", () => $("detailModal").classList.add("hidden"));
$("closeNoteBtn").addEventListener("click", () => $("noteModal").classList.add("hidden"));
$("noteModal").addEventListener("click", (e) => { if (e.target === $("noteModal")) $("noteModal").classList.add("hidden"); });
$("settingsBtn").addEventListener("click", openSettings);
$("closeSettingsBtn").addEventListener("click", closeSettings);
$("settingsModal").addEventListener("click", (e) => { if (e.target === $("settingsModal")) closeSettings(); });
for (const btn of document.querySelectorAll(".settings-tab")) {
  btn.addEventListener("click", () => showSettingsPanel(btn.dataset.settingsPanel));
}
for (const btn of document.querySelectorAll(".tab-btn")) {
  btn.addEventListener("click", () => showTab(btn.dataset.tab));
}

for (const id of ["librarySearch", "filterTag", "filterCsi", "filterBookmarked"]) {
  $(id).addEventListener("input", debounce(loadLibrary, 250));
  $(id).addEventListener("change", loadLibrary);
}
for (const id of ["filterProjects", "filterDesignTeams", "filterDisciplines"]) {
  $(id).addEventListener("change", loadLibrary);
}

for (const menu of document.querySelectorAll("details.multi-filter")) {
  let hideTimer = null;
  menu.addEventListener("mouseenter", () => clearTimeout(hideTimer));
  menu.addEventListener("mouseleave", () => {
    hideTimer = setTimeout(() => menu.removeAttribute("open"), 300);
  });
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
    addSelectedFiles(e.dataTransfer.files);
  }
});
fileInput.addEventListener("change", () => addSelectedFiles(fileInput.files));
for (const id of ["projectName", "designTeam"]) {
  $(id).addEventListener("input", () => {
    $(id).classList.toggle("input-error", !$(id).value.trim());
    updateUploadButtonState();
  });
}

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

let uploadEntries = [];
let uploadQueueRunning = false;
let projectCreatePromise = null;
let processingStarted = false;
let backgroundStatusTimer = null;

$("uploadBtn").addEventListener("click", processUploadedFiles);

function addSelectedFiles(fileList) {
  if (!validateUploadMetadata()) {
    fileInput.value = "";
    return;
  }
  if (processingStarted) {
    alert("This drawing set is already processing. Finish this review before starting another upload batch.");
    fileInput.value = "";
    return;
  }

  const rejected = [];
  for (const file of Array.from(fileList || [])) {
    if (file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) {
      rejected.push(file.name);
      continue;
    }
    const entry = { id: `${Date.now()}_${Math.random().toString(36).slice(2)}`, file, progress: 0, status: "queued", error: "" };
    uploadEntries.push(entry);
  }
  if (rejected.length) alert(`Only PDF files can be uploaded: ${rejected.join(", ")}`);
  renderSelectedFiles();
  fileInput.value = "";
  uploadQueuedFiles();
}

function removeSelectedFile(id) {
  const entry = uploadEntries.find(e => e.id === id);
  if (entry && ["uploading", "done", "processing"].includes(entry.status)) return;
  uploadEntries = uploadEntries.filter(e => e.id !== id);
  renderSelectedFiles();
}

function renderSelectedFiles() {
  const container = $("selectedFiles");
  container.innerHTML = "";
  for (const entry of uploadEntries) {
    const item = document.createElement("div");
    item.className = `selected-file ${entry.status}`;
    const statusLabel = entry.status === "error" ? (entry.error || "Failed")
      : entry.status === "done" ? "Uploaded"
      : entry.status === "uploading" ? `${entry.progress}%`
      : entry.status === "processing" ? "Processing"
      : "Queued";
    const canRemove = !["uploading", "done", "processing"].includes(entry.status);
    item.innerHTML = `
      <span class="selected-file-name" title="${escapeAttr(entry.file.name)}">${escapeHtml(entry.file.name)}</span>
      <div class="selected-file-bar" aria-label="Upload progress for ${escapeAttr(entry.file.name)}"><div class="selected-file-fill" style="width:${entry.progress}%"></div></div>
      <span class="selected-file-status${entry.status === "error" ? " error" : ""}">${escapeHtml(statusLabel)}</span>
      <button type="button" class="remove-file-btn" aria-label="Remove ${escapeAttr(entry.file.name)}" ${canRemove ? "" : "disabled"}>×</button>
    `;
    item.querySelector(".remove-file-btn").addEventListener("click", () => removeSelectedFile(entry.id));
    container.appendChild(item);
  }
  updateUploadButtonState();
}

function updateUploadButtonState() {
  const btn = $("uploadBtn");
  const hasFiles = uploadEntries.length > 0;
  const busyUploading = uploadQueueRunning || uploadEntries.some(e => e.status === "uploading");
  const hasErrors = uploadEntries.some(e => e.status === "error");
  const allUploaded = hasFiles && uploadEntries.every(e => e.status === "done");
  const hasRequiredMetadata = Boolean($("projectName").value.trim() && $("designTeam").value.trim());
  btn.disabled = processingStarted || busyUploading || !allUploaded || hasErrors || !hasRequiredMetadata;
  if (processingStarted) {
    btn.textContent = "Processing...";
  } else if (busyUploading) {
    btn.textContent = uploadEntries.some(e => e.status === "uploading") ? "Uploading..." : "Preparing upload...";
  } else if (hasErrors) {
    btn.textContent = "Fix Upload Errors";
  } else if (allUploaded) {
    btn.textContent = "Upload & Process";
  } else {
    btn.textContent = "Upload & Process";
  }
}

async function uploadQueuedFiles() {
  if (uploadQueueRunning || processingStarted) return;
  const queued = uploadEntries.filter(e => e.status === "queued");
  if (!queued.length) {
    updateUploadButtonState();
    return;
  }

  resetReviewWorkspace();
  $("reviewPanel").classList.add("hidden");
  $("processingStatus").classList.add("hidden");
  uploadQueueRunning = true;
  updateUploadButtonState();

  try {
    await ensureUploadProject();
    let entry = uploadEntries.find(e => e.status === "queued");
    while (entry) {
      try {
        await uploadSingleFile(entry);
      } catch (err) {
        entry.status = "error";
        entry.error = err?.message || "Upload failed";
        entry.progress = 100;
        renderSelectedFiles();
      }
      entry = uploadEntries.find(e => e.status === "queued");
    }
  } catch (err) {
    const message = err?.message || "Could not prepare uploads.";
    for (const entry of uploadEntries.filter(e => e.status === "queued" || e.status === "uploading")) {
      entry.status = "error";
      entry.error = message;
    }
    renderSelectedFiles();
  } finally {
    uploadQueueRunning = false;
    updateUploadButtonState();
  }
}

async function ensureUploadProject() {
  if (projectId) return manifest;
  if (!projectCreatePromise) {
    projectCreatePromise = createEmptyProject()
      .then((createdManifest) => {
        manifest = createdManifest;
        projectId = createdManifest.project_id;
        return createdManifest;
      })
      .catch((err) => {
        projectCreatePromise = null;
        throw err;
      });
  }
  return projectCreatePromise;
}

async function processUploadedFiles() {
  if (processingStarted) return;
  if (!validateUploadMetadata()) return;
  if (!uploadEntries.length) {
    alert("Choose one or more PDFs first.");
    return;
  }
  if (uploadEntries.some(e => e.status === "error")) {
    alert("One or more files failed to upload. Remove failed files or drop them again before processing.");
    return;
  }
  if (uploadEntries.some(e => e.status !== "done")) {
    alert("Please wait for all files to finish uploading before processing.");
    return;
  }

  try {
    processingStarted = true;
    for (const entry of uploadEntries) entry.status = "processing";
    renderSelectedFiles();
    resetReviewWorkspace();
    $("reviewPanel").classList.add("hidden");

    const processRes = await fetch(`/api/projects/${projectId}/process`, { method: "POST" });
    if (!processRes.ok) throw new Error(await responseErrorMessage(processRes, "Could not start processing."));

    manifest = await processRes.json();
    pageIndex = 0;
    $("reviewPanel").classList.remove("hidden");
    renderProcessingStatus(manifest.processing_status);
    startPolling();
    await loadNextReady(-1);
    await loadDesignTeams();
    await loadLibraryFacets();
  } catch (err) {
    processingStarted = false;
    for (const entry of uploadEntries) entry.status = "done";
    renderSelectedFiles();
    alert(err?.message || "Could not start processing.");
  } finally {
    updateUploadButtonState();
  }
}

function validateUploadMetadata() {
  const required = [$("projectName"), $("designTeam")];
  for (const input of required) input.classList.toggle("input-error", !input.value.trim());
  const missing = required.filter(input => !input.value.trim());
  if (missing.length) {
    missing[0].focus();
    alert("Project Name and Design Team / Architect are required before uploading drawings.");
    return false;
  }
  return true;
}

async function createEmptyProject() {
  const form = new FormData();
  form.append("project_name", $("projectName").value || "");
  form.append("design_team", $("designTeam").value || "");
  form.append("discipline", $("discipline").value || "unknown");
  form.append("designers", JSON.stringify(collectDesigners()));
  const res = await fetch("/api/projects/init", { method: "POST", body: form });
  if (!res.ok) throw new Error(await responseErrorMessage(res, "Could not create the project."));
  return res.json();
}

function uploadSingleFile(entry) {
  return new Promise((resolve, reject) => {
    entry.status = "uploading";
    entry.progress = 0;
    entry.error = "";
    renderSelectedFiles();

    const form = new FormData();
    form.append("file", entry.file);
    form.append("process", "false");

    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/projects/${projectId}/sources`);
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      entry.progress = Math.max(1, Math.min(99, Math.round((e.loaded / e.total) * 100)));
      renderSelectedFiles();
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        entry.progress = 100;
        entry.status = "done";
        try { manifest = JSON.parse(xhr.responseText); } catch {}
        renderSelectedFiles();
        resolve();
      } else {
        responseErrorMessage(xhr, "Upload failed").then((detail) => {
          reject(new Error(`${entry.file.name}: ${detail}`));
        });
      }
    };
    xhr.onerror = () => reject(new Error(`${entry.file.name}: Upload failed`));
    xhr.send(form);
  });
}

async function responseErrorMessage(responseLike, fallback) {
  try {
    if (typeof responseLike.json === "function") {
      const data = await responseLike.json();
      return data.detail || fallback;
    }
    const data = JSON.parse(responseLike.responseText || "{}");
    return data.detail || responseLike.statusText || fallback;
  } catch {
    return responseLike.statusText || fallback;
  }
}

function resetReviewWorkspace() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  pageIndex = 0;
  boxes = [];
  selectedId = null;
  loadedPageId = null;
  sheetBox = null;
  const img = $("sheetImage");
  if (img) img.removeAttribute("src");
  $("boxLayer").innerHTML = "";
  $("boxList").innerHTML = "";
  $("detailsList").innerHTML = "";
  $("sheetInfo").textContent = "";
}

updateUploadButtonState();
startBackgroundStatusPolling();

function startBackgroundStatusPolling() {
  if (backgroundStatusTimer) clearInterval(backgroundStatusTimer);
  refreshBackgroundStatus();
  backgroundStatusTimer = setInterval(refreshBackgroundStatus, 2500);
}

async function refreshBackgroundStatus() {
  const localUploading = uploadEntries.filter(e => e.status === "queued" || e.status === "uploading").length;
  const localProcessing = uploadEntries.filter(e => e.status === "processing").length;
  let status = null;
  try {
    const res = await fetch("/api/background-status");
    if (res.ok) status = await res.json();
  } catch {}
  renderBackgroundBar(status, localUploading, localProcessing);
}

function renderBackgroundBar(status, localUploading = 0, localProcessing = 0) {
  const bar = $("backgroundBar");
  if (!bar) return;
  const activePages = status?.active_pages || 0;
  const pages = status?.pages || {};
  const ai = status?.ai_jobs || {};
  const activeAi = status?.active_ai_jobs || 0;
  const hasWork = localUploading > 0 || localProcessing > 0 || activePages > 0 || activeAi > 0;
  bar.classList.toggle("hidden", !hasWork);
  if (!hasWork) return;

  const pageParts = [];
  if (localUploading) pageParts.push(`${localUploading} uploading`);
  if (activePages) pageParts.push(`${activePages} rendering/detecting`);
  if (localProcessing && !activePages) pageParts.push("starting");
  $("backgroundBarTextPages").textContent = pageParts.join(" • ") || "idle";

  const aiParts = [];
  if (activeAi) aiParts.push(`${activeAi} pending/running`);
  $("backgroundBarTextAi").textContent = aiParts.join(" • ") || "idle";

  const completedAi = ai.complete || 0;
  const totalAi = completedAi + activeAi + (ai.failed || 0);

  const localPagesTotal = manifest?.pages?.length || 0;
  const localPagesDone = manifest?.pages?.filter(p => p.status && p.status !== "pending" && p.status !== "processing").length || 0;
  const pagesTotal = localPagesTotal + uploadEntries.length;
  const pagesDone = localPagesDone + uploadEntries.filter(e => e.status === "done" || e.status === "processing").length;
  const pagesPct = pagesTotal ? Math.max(4, Math.min(100, Math.round((pagesDone / pagesTotal) * 100))) : 12;
  $("backgroundBarFillPages").style.width = `${pagesPct}%`;
  $("backgroundBarFillPages").classList.toggle("indeterminate", pagesTotal === 0 && activePages > 0);

  const aiPct = totalAi ? Math.max(4, Math.min(100, Math.round((completedAi / totalAi) * 100))) : 12;
  $("backgroundBarFillAi").style.width = `${aiPct}%`;
  $("backgroundBarFillAi").classList.toggle("indeterminate", totalAi === 0 && activeAi > 0);
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
  if (tabId === "compareTab") renderComparison();
}

function setCompareMode(enabled) {
  compareMode = Boolean(enabled);
  document.body.classList.toggle("compare-mode", compareMode);
  $("libraryPanel")?.classList.toggle("compare-mode", compareMode);
  updateCompareModeButton();
  updateVisibleCompareControls();
}

function enableCompareMode({ switchToLibrary = false } = {}) {
  setCompareMode(true);
  if (switchToLibrary) showTab("libraryTab");
}

async function loadDesignTeams() {
  const res = await fetch("/api/design-teams");
  if (!res.ok) return;
  const data = await res.json();
  const list = $("designTeamOptions");
  list.innerHTML = "";
  const firmList = $("firmNameOptions");
  firmList.innerHTML = "";
  for (const team of data.design_teams) {
    const option = document.createElement("option");
    option.value = team.name;
    list.appendChild(option);
    const firmOption = document.createElement("option");
    firmOption.value = team.name;
    firmList.appendChild(firmOption);
  }
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

let sheetOverlayShownAt = 0;
const SHEET_OVERLAY_MIN_MS = 400;

function showSheetLoadingOverlay() {
  sheetOverlayShownAt = Date.now();
  $("sheetLoadingOverlay").classList.remove("hidden");
}

function hideSheetLoadingOverlay() {
  const elapsed = Date.now() - sheetOverlayShownAt;
  if (sheetOverlayShownAt && elapsed < SHEET_OVERLAY_MIN_MS) {
    setTimeout(() => $("sheetLoadingOverlay").classList.add("hidden"), SHEET_OVERLAY_MIN_MS - elapsed);
  } else {
    $("sheetLoadingOverlay").classList.add("hidden");
  }
  sheetOverlayShownAt = 0;
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
    hideSheetLoadingOverlay();
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
  currentMergeTargetIds = [];

  const img = $("sheetImage");
  loadedPageId = null;
  img.onload = () => {
    hideSheetLoadingOverlay();
    loadedPageId = page.id;
    $("boxLayer").style.width = `${img.naturalWidth}px`;
    $("boxLayer").style.height = `${img.naturalHeight}px`;
    $("sheetStage").style.width = `${img.naturalWidth}px`;
    $("sheetStage").style.height = `${img.naturalHeight}px`;
    sheetBox = clampSheetBox(page.sheet_box || manifest.last_sheet_box || defaultSheetBox(img), img);
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
    el.className = "crop-box" + (box.id === selectedId ? " selected" : "") + (currentMergeTargetIds.includes(box.id) ? " merge-target" : "");
    el.style.left = `${box.x}px`; el.style.top = `${box.y}px`; el.style.width = `${box.w}px`; el.style.height = `${box.h}px`;
    el.style.borderWidth = `${borderPx}px`; el.dataset.id = box.id; el.style.cursor = "move";
    const label = document.createElement("div");
    label.className = "label"; label.textContent = box.id; label.style.fontSize = `${12 / zoom}px`; label.style.top = `${-22 / zoom}px`; label.style.padding = `${2 / zoom}px ${5 / zoom}px`;
    el.appendChild(label);
    el.addEventListener("mousemove", (e) => { if (!dragState) el.style.cursor = cursorForMode(getResizeModeFromMouseEvent(e, el)); });
    el.addEventListener("mousedown", (e) => { if (e.button !== 0) return; e.stopPropagation(); startDrag(e, box.id, getResizeModeFromMouseEvent(e, el)); });
    layer.appendChild(el);
  }
  renderSheetBox();
}

function defaultSheetBox(img) {
  const w = Math.min(300, img.naturalWidth * 0.25);
  const h = Math.min(150, img.naturalHeight * 0.12);
  const margin = 20;
  return { x: img.naturalWidth - w - margin, y: img.naturalHeight - h - margin, w, h };
}

function clampSheetBox(box, img) {
  const w = Math.min(box.w, img.naturalWidth);
  const h = Math.min(box.h, img.naturalHeight);
  return {
    x: Math.max(0, Math.min(img.naturalWidth - w, box.x)),
    y: Math.max(0, Math.min(img.naturalHeight - h, box.y)),
    w, h,
  };
}

function renderSheetBox() {
  if (!sheetBox) return;
  const layer = $("boxLayer");
  const el = document.createElement("div");
  el.className = "sheet-num-box";
  el.style.left = `${sheetBox.x}px`; el.style.top = `${sheetBox.y}px`; el.style.width = `${sheetBox.w}px`; el.style.height = `${sheetBox.h}px`;
  const borderPx = Math.max(1, 3 / zoom);
  el.style.borderWidth = `${borderPx}px`;
  const label = document.createElement("div");
  label.className = "label"; label.textContent = "Sheet #";
  label.style.fontSize = `${12 / zoom}px`; label.style.top = `${-22 / zoom}px`; label.style.padding = `${2 / zoom}px ${5 / zoom}px`;
  el.appendChild(label);
  el.addEventListener("mousemove", (e) => { if (!sheetDragState) el.style.cursor = cursorForMode(getResizeModeFromMouseEvent(e, el)); });
  el.addEventListener("mousedown", (e) => { if (e.button !== 0) return; e.stopPropagation(); startSheetDrag(e, getResizeModeFromMouseEvent(e, el)); });
  layer.appendChild(el);
}

function startSheetDrag(e, mode) {
  sheetDragState = { mode, startX: e.clientX, startY: e.clientY, orig: { ...sheetBox } };
  document.addEventListener("mousemove", onSheetDrag);
  document.addEventListener("mouseup", stopSheetDrag);
}

function onSheetDrag(e) {
  if (!sheetDragState) return;
  const dx = (e.clientX - sheetDragState.startX) / zoom;
  const dy = (e.clientY - sheetDragState.startY) / zoom;
  const o = sheetDragState.orig;
  const mode = sheetDragState.mode;
  const minSize = 30;
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
  const img = $("sheetImage");
  sheetBox = clampSheetBox({ x, y, w: Math.max(minSize, w), h: Math.max(minSize, h) }, img);
  renderBoxes();
}

function stopSheetDrag() {
  sheetDragState = null;
  document.removeEventListener("mousemove", onSheetDrag);
  document.removeEventListener("mouseup", stopSheetDrag);
}

function renderBoxList() {
  const list = $("boxList");
  list.innerHTML = "";
  boxes.forEach((box, i) => {
    const li = document.createElement("li");
    li.textContent = `${i + 1}. ${Math.round(box.w)} × ${Math.round(box.h)} — ${box.source}`;
    li.className = box.id === selectedId ? "selected" : "";
    li.onclick = () => { selectedId = box.id; currentMergeTargetIds = []; renderBoxes(); renderBoxList(); };
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
  currentMergeTargetIds = findMergeTargets(box.id).map(b => b.id);
  renderBoxes(); renderBoxList();
}

function stopDrag() {
  if (dragState && dragState.moved) applyOverlapMerge(dragState.id);
  dragState = null; currentMergeTargetIds = [];
  document.removeEventListener("mousemove", onDrag);
  document.removeEventListener("mouseup", stopDrag);
  renderBoxes(); renderBoxList();
}

function boxArea(b) { return b.w * b.h; }
function intersectionArea(a, b) { const x0 = Math.max(a.x, b.x); const y0 = Math.max(a.y, b.y); const x1 = Math.min(a.x + a.w, b.x + b.w); const y1 = Math.min(a.y + a.h, b.y + b.h); return Math.max(0, x1 - x0) * Math.max(0, y1 - y0); }
function unionBox(a, b) { const x0 = Math.min(a.x, b.x); const y0 = Math.min(a.y, b.y); const x1 = Math.max(a.x + a.w, b.x + b.w); const y1 = Math.max(a.y + a.h, b.y + b.h); return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }; }
function findMergeTargets(activeId) {
  const active = boxes.find(b => b.id === activeId);
  if (!active) return [];
  return boxes
    .filter(other => {
      if (other.id === activeId) return false;
      const ratio = intersectionArea(active, other) / Math.max(1, Math.min(boxArea(active), boxArea(other)));
      return ratio >= 0.50;
    })
    .sort((a, b) => boxArea(b) - boxArea(a));
}
function applyOverlapMerge(activeId) {
  const active = boxes.find(b => b.id === activeId);
  if (!active) return;
  const targets = findMergeTargets(activeId);
  if (!targets.length) return;

  const mergeGroup = [active, ...targets];
  const keep = mergeGroup.reduce((largest, box) => boxArea(box) > boxArea(largest) ? box : largest, active);
  const mergedBounds = mergeGroup.reduce((bounds, box) => unionBox(bounds, box), keep);

  keep.x = mergedBounds.x; keep.y = mergedBounds.y; keep.w = mergedBounds.w; keep.h = mergedBounds.h;
  keep.source = keep.source.includes("merged") ? keep.source : `${keep.source}+merged`;
  const removeIds = new Set(mergeGroup.filter(box => box.id !== keep.id).map(box => box.id));
  boxes = boxes.filter(box => !removeIds.has(box.id));
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
  currentMergeTargetIds = [];
  renderBoxes();
  renderBoxList();
}
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
    currentMergeTargetIds = [];
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
  showSheetLoadingOverlay();
  const res = await fetch("/api/approve-sheet", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ project_id: projectId, page_id: page.id, boxes, sheet_box: sheetBox })
  });
  if (!res.ok) { hideSheetLoadingOverlay(); alert(await res.text()); return; }
  const data = await res.json();
  page.status = "approved"; page.boxes = structuredClone(boxes); page.approved = true;
  page.sheet_box = structuredClone(sheetBox);
  manifest.last_sheet_box = structuredClone(sheetBox);
  renderProcessingStatus(data.processing_status);
  loadDetails();
  loadLibrary();
  loadLibraryFacets();
  await loadNextReady(page.page_index);
}

async function skipSheet() {
  if (!projectId) return;
  const page = manifest.pages.find(p => p.page_index === pageIndex);
  if (!page) return;
  showSheetLoadingOverlay();
  const res = await fetch("/api/skip-sheet", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ project_id: projectId, page_id: page.id }) });
  if (!res.ok) { hideSheetLoadingOverlay(); alert(await res.text()); return; }
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


function disciplineLabel(discipline) {
  const normalized = normalizeDiscipline(discipline);
  const labels = {
    architectural: "Architectural",
    structural: "Structural",
    civil: "Civil",
    mechanical: "Mechanical/HVAC",
    electrical: "Electrical",
    plumbing: "Plumbing",
    "fire protection": "Fire Suppression",
    "technology/security": "Technology",
    unknown: "Unknown",
  };
  return labels[normalized] || titleCase(discipline || "unknown");
}

function normalizeDiscipline(discipline) {
  const value = String(discipline || "unknown").trim().toLowerCase();
  if (value.includes("civil")) return "civil";
  if (value.includes("struct")) return "structural";
  if (value.includes("arch")) return "architectural";
  if (value.includes("mech") || value.includes("hvac")) return "mechanical";
  if (value.includes("plumb")) return "plumbing";
  if (value.includes("elect")) return "electrical";
  if (value.includes("tech") || value.includes("security")) return "technology/security";
  if (value.includes("fire")) return "fire protection";
  return value || "unknown";
}

function disciplineClass(discipline) {
  return `discipline-${normalizeDiscipline(discipline).replace(/[^a-z0-9]+/g, "-")}`;
}

function disciplineBadge(discipline) {
  return `<span class="discipline-badge ${disciplineClass(discipline)}">${escapeHtml(disciplineLabel(discipline))}</span>`;
}

function titleCase(value) {
  return String(value || "").replace(/[-_/]+/g, " ").replace(/\b\w/g, c => c.toUpperCase());
}

function toggleCompareMode() {
  setCompareMode(!compareMode);
}

function updateCompareModeButton() {
  const btn = $("compareModeBtn");
  if (!btn) return;
  btn.textContent = compareMode ? "Done Selecting" : "Compare Details";
  btn.classList.toggle("primary-btn", compareMode);
  btn.setAttribute("aria-pressed", compareMode ? "true" : "false");
}

function setDetailCompared(detail, compared) {
  const detailId = String(detail.id);
  if (compared) {
    compareDetailIds.add(detailId);
    compareDetails.set(detailId, detail);
  } else {
    compareDetailIds.delete(detailId);
    compareDetails.delete(detailId);
  }
  renderComparison();
  updateVisibleCompareControls();
}

function updateVisibleCompareControls() {
  document.querySelectorAll(".compare-select").forEach(input => {
    input.checked = compareDetailIds.has(input.value);
  });
}

function clearComparison() {
  compareDetailIds.clear();
  compareDetails.clear();
  updateVisibleCompareControls();
  renderComparison();
  loadLibrary();
}

function renderComparison() {
  const results = $("compareResults");
  if (!results) return;
  const details = Array.from(compareDetailIds).map(id => compareDetails.get(id)).filter(Boolean);
  const summary = $("compareSummary");
  if (summary) summary.textContent = details.length ? `${details.length} detail${details.length === 1 ? "" : "s"} selected for comparison.` : "Select details from the library to compare them side by side.";
  results.innerHTML = "";
  if (!details.length) {
    results.innerHTML = `<div class="empty-compare"><strong>No details selected yet.</strong><br>Use the Detail Library’s Compare Details button to turn on card checkboxes, then select details to compare.</div>`;
    return;
  }
  for (const d of details) results.appendChild(detailCard(d, false, false, true));
}

function detailCard(d, compact = false, list = false, comparing = false) {
  const card = document.createElement("div");
  const detailId = String(d.id);
  card.className = "detail-card" + (d.bookmarked ? " bookmarked" : "") + (list ? " list-card" : "") + (compareDetailIds.has(detailId) ? " compared" : "") + (comparing ? " compare-card" : "");
  const hasNote = Boolean((d.notes || "").trim());
  const noteBadge = hasNote ? `<button class="note-badge" type="button" aria-label="View note" title="View note">📝</button>` : "";
  const comparePicker = (!compact && !comparing) ? `<label class="compare-picker" title="Select detail for comparison"><input class="compare-select" type="checkbox" value="${escapeAttr(detailId)}" ${compareDetailIds.has(detailId) ? "checked" : ""} /> Compare</label>` : "";
  const thumb = `<div class="card-thumb-wrap"><img src="/data/projects/${d.project_id}/${d.thumbnail || d.crop_image}" alt="Detail thumbnail" /><button class="bookmark-badge" type="button" aria-label="Toggle bookmark">${d.bookmarked ? "★" : "☆"}</button>${noteBadge}${comparePicker}</div>`;
  const badge = disciplineBadge(d.discipline || "unknown");

  if (list) {
    const tags = (d.tags || []).map(t => `<span class="tag">${escapeHtml(t)}</span>`).join(" ");
    const numbers = [d.sheet_number ? `Sheet ${d.sheet_number}` : "", d.detail_number ? `Detail ${d.detail_number}` : ""].filter(Boolean).join(" · ");
    const csi = (d.csi_divisions || []).join(", ");
    const summary = d.summary || d.searchable_description || "";
    card.innerHTML = `
      ${thumb}
      <div class="list-card-info">
        <strong>${escapeHtml(d.detail_title || "Untitled detail")}</strong>
        <small>${escapeHtml(d.project_name || "Unnamed project")}${d.design_team ? ` — ${escapeHtml(d.design_team)}` : ""}</small>
        <div class="card-meta-row">${badge}${numbers ? `<small>${escapeHtml(numbers)}</small>` : ""}</div>
        ${summary ? `<p class="list-summary">${escapeHtml(summary)}</p>` : ""}
        ${tags ? `<div class="list-tags">${tags}</div>` : ""}
        <div class="list-meta">
          ${csi ? `<small><span class="meta-label">CSI:</span> ${escapeHtml(csi)}</small>` : ""}
          ${d.assembly_system_type ? `<small><span class="meta-label">Assembly/System:</span> ${escapeHtml(d.assembly_system_type)}</small>` : ""}
        </div>
      </div>
    `;
  } else {
    card.innerHTML = `
      ${thumb}
      <strong>${escapeHtml(d.detail_title || "Untitled detail")}</strong><br>
      <small>${escapeHtml(d.project_name || "Unnamed project")}</small><br>
      <div class="card-meta-row">${badge}</div>
    `;
  }
  card.querySelector(".bookmark-badge").addEventListener("click", async (e) => {
    e.stopPropagation();
    await toggleBookmark(d);
  });
  const noteBtn = card.querySelector(".note-badge");
  if (noteBtn) noteBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    showNotePopup(d);
  });
  const compareSelect = card.querySelector(".compare-select");
  const comparePickerLabel = card.querySelector(".compare-picker");
  if (comparePickerLabel && compareSelect) comparePickerLabel.addEventListener("click", (e) => {
    e.stopPropagation();
    if (e.target === compareSelect) return;
    e.preventDefault();
    compareSelect.checked = !compareSelect.checked;
    compareSelect.dispatchEvent(new Event("change", { bubbles: true }));
  });
  if (compareSelect) compareSelect.addEventListener("click", (e) => e.stopPropagation());
  if (compareSelect) compareSelect.addEventListener("change", (e) => {
    e.stopPropagation();
    setDetailCompared(d, compareSelect.checked);
    card.classList.toggle("compared", compareSelect.checked);
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
let highlightedScanDetailIds = new Set();

function setScanStatus(text) {
  const span = $("scanStatus");
  span.textContent = text || "";
  span.classList.toggle("hidden", !text);
}

function scanDetailLabel(detail) {
  if (!detail) return "detail";
  const title = detail.detail_title || "Untitled detail";
  const sheet = detail.sheet_number ? `sheet ${detail.sheet_number}` : (detail.page_number ? `page ${detail.page_number}` : "");
  return [title, sheet].filter(Boolean).join(" on ");
}

function cssEscape(value) {
  if (window.CSS && typeof window.CSS.escape === "function") return window.CSS.escape(value);
  return String(value).replace(/[^a-zA-Z0-9_-]/g, "\\$&");
}

function updateScanHighlights(status = {}) {
  highlightedScanDetailIds = new Set((status.running_detail_ids || []).map(String));
  document.querySelectorAll(".detail-card.scan-running").forEach(card => {
    if (!highlightedScanDetailIds.has(card.dataset.detailId)) card.classList.remove("scan-running");
  });
  for (const id of highlightedScanDetailIds) {
    document.querySelectorAll(`.detail-card[data-detail-id="${cssEscape(id)}"]`).forEach(card => card.classList.add("scan-running"));
  }
}

function scanStatusMessage(status) {
  const running = status.running_details || [];
  const active = status.active || 0;
  if (running.length === 1) return `AI scanning ${scanDetailLabel(running[0])} (${active} job${active === 1 ? "" : "s"} remaining)...`;
  if (running.length > 1) return `AI scanning ${running.length} details (${active} job${active === 1 ? "" : "s"} remaining)...`;
  return `AI scanning in background: ${active} job${active === 1 ? "" : "s"} remaining...`;
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
    updateScanHighlights(data.status);
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
    updateScanHighlights(s);
    if (s.active > 0) {
      setScanStatus(scanStatusMessage(s));
    } else {
      updateScanHighlights({ running_detail_ids: [] });
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

async function openSettings() {
  $("settingsModal").classList.remove("hidden");
  setSettingsMessage("");
  await loadSettingsEntities();
}

function closeSettings() {
  $("settingsModal").classList.add("hidden");
}

function showSettingsPanel(panelId) {
  for (const panel of document.querySelectorAll(".settings-panel")) {
    panel.classList.toggle("hidden", panel.id !== panelId);
    panel.classList.toggle("active", panel.id === panelId);
  }
  for (const btn of document.querySelectorAll(".settings-tab")) {
    btn.classList.toggle("active", btn.dataset.settingsPanel === panelId);
  }
}

function setSettingsMessage(message, isError = false) {
  const el = $("settingsMessage");
  el.textContent = message || "";
  el.classList.toggle("hidden", !message);
  el.classList.toggle("error", Boolean(isError));
}

async function loadSettingsEntities() {
  const res = await fetch("/api/manage/entities");
  if (!res.ok) {
    setSettingsMessage(await res.text(), true);
    return;
  }
  const data = await res.json();
  renderSettingsProjects(data.projects || []);
  renderSettingsFirms(data.design_teams || []);
}

function renderSettingsProjects(projects) {
  const container = $("settingsProjectsList");
  container.innerHTML = "";
  if (!projects.length) {
    container.innerHTML = `<div class="settings-empty">No projects have been created yet.</div>`;
    return;
  }
  for (const project of projects) {
    const name = project.project_name || "";
    const displayName = name || "(blank project name)";
    const item = document.createElement("article");
    item.className = "settings-entity";
    item.innerHTML = `
      <div class="settings-entity-title">
        <div>
          <strong>${escapeHtml(displayName)}</strong>
          <small>Architect / design firm: ${escapeHtml(project.design_team || "None")}</small>
        </div>
        <span class="settings-entity-meta">${Number(project.detail_count || 0)} details • ${Number(project.source_count || 0)} source PDFs</span>
      </div>
      ${settingsDeleteForm("project", project.id, name, displayName)}
    `;
    wireSettingsDeleteForm(item, "project", project.id, name);
    container.appendChild(item);
  }
}

function renderSettingsFirms(firms) {
  const container = $("settingsFirmsList");
  container.innerHTML = "";
  if (!firms.length) {
    container.innerHTML = `<div class="settings-empty">No design firms have been saved yet.</div>`;
    return;
  }
  for (const firm of firms) {
    const name = firm.name || "";
    const primaryCount = Number(firm.primary_project_count || 0);
    const designerCount = Number(firm.designer_project_count || 0);
    const item = document.createElement("article");
    item.className = "settings-entity";
    item.innerHTML = `
      <div class="settings-entity-title">
        <div>
          <strong>${escapeHtml(name)}</strong>
          <small>${primaryCount} primary projects • ${designerCount} engineer listings</small>
        </div>
        <span class="settings-entity-meta">${Number(firm.detail_count || 0)} associated details</span>
      </div>
      ${settingsDeleteForm("firm", firm.id, name, name)}
    `;
    wireSettingsDeleteForm(item, "firm", firm.id, name);
    container.appendChild(item);
  }
}

function settingsDeleteForm(type, id, confirmName, displayName) {
  const itemLabel = type === "project" ? "project" : "design firm";
  const associationLabel = type === "project" ? "all pages, crops, details, and source files in this project" : "all projects, source files, crops, and details associated with this firm";
  const metadataLabel = type === "project" ? "only the project name, primary firm, and engineer firm labels; keep this project's details" : "only this firm's name/labels; keep associated project details";
  return `
    <div class="settings-delete-form" data-type="${escapeAttr(type)}" data-id="${escapeAttr(id)}" data-confirm-name="${escapeAttr(confirmName)}">
      <div class="settings-delete-options" role="radiogroup" aria-label="Delete mode for ${escapeAttr(displayName)}">
        <label><input type="radio" name="deleteMode_${escapeAttr(type)}_${escapeAttr(id)}" value="metadata" checked /> Delete ${metadataLabel}.</label>
        <label><input type="radio" name="deleteMode_${escapeAttr(type)}_${escapeAttr(id)}" value="items" /> Delete ${associationLabel}.</label>
      </div>
      <div class="settings-confirm-row">
        <label>Type <code>${escapeHtml(confirmName || "(blank)")}</code> to confirm
          <input type="text" class="settings-confirm-input" autocomplete="off" ${confirmName ? "" : "placeholder=\"Leave blank to confirm blank name\""} />
        </label>
        <button type="button" class="danger-btn settings-delete-btn" disabled>Delete ${escapeHtml(itemLabel)}</button>
      </div>
    </div>
  `;
}

function wireSettingsDeleteForm(item, type, id, confirmName) {
  const input = item.querySelector(".settings-confirm-input");
  const btn = item.querySelector(".settings-delete-btn");
  const update = () => { btn.disabled = input.value.trim() !== confirmName.trim(); };
  input.addEventListener("input", update);
  update();
  btn.addEventListener("click", async () => {
    const mode = item.querySelector("input[type=radio]:checked")?.value || "metadata";
    const deleteItems = mode === "items";
    const itemLabel = type === "project" ? "project" : "design firm";
    const warning = deleteItems
      ? `This will permanently delete the ${itemLabel} and all associated items. Continue?`
      : `This will delete only the ${itemLabel} name/firm labels and keep associated details. Continue?`;
    if (!confirm(warning)) return;
    btn.disabled = true;
    btn.textContent = "Deleting...";
    try {
      const endpoint = type === "project" ? `/api/manage/projects/${id}` : `/api/manage/design-teams/${id}`;
      const res = await fetch(endpoint, {
        method: "DELETE",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ delete_items: deleteItems, confirm_name: input.value.trim() }),
      });
      if (!res.ok) throw new Error(await res.text());
      const result = await res.json();
      setSettingsMessage(`${deleteItems ? "Deleted associated items for" : "Removed labels for"} ${result.project_name || result.name || itemLabel}.`);
      await loadSettingsEntities();
      await loadLibraryFacets();
      await loadLibrary();
      const deletedCurrentProject = deleteItems && (result.project_id === projectId || (result.project_ids || []).includes(projectId));
      if (deletedCurrentProject) {
        projectId = null;
        manifest = null;
        resetReviewWorkspace();
        $("reviewPanel").classList.add("hidden");
        $("detailsList").innerHTML = "";
      } else if (projectId) {
        await refreshProjectStatus();
        await loadDetails();
      }
    } catch (err) {
      setSettingsMessage(err?.message || "Delete failed.", true);
      btn.disabled = false;
      btn.textContent = `Delete ${itemLabel}`;
    }
  });
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
  renderCheckboxOptions(
    "filterDesignTeams",
    (facets.design_teams || []).map(team => ({ value: team.id, label: team.name })),
    "No design teams yet"
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
    design_teams: selectedCheckboxValues("filterDesignTeams").join(","),
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
  for (const d of data.details) div.appendChild(detailCard(d, false, !libraryGrid));
  updateVisibleCompareControls();
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
            <span>Architect: ${escapeHtml(d.design_team || "Unknown")}</span>
            ${(d.designers || []).filter(x => x.discipline !== "architectural" && x.firm_name).map(x => `<span>${escapeHtml(ENGINEER_LABELS[x.discipline] || x.discipline)}: ${escapeHtml(x.firm_name)}</span>`).join("")}
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
        <div class="designers-section">
          <h3>Other Engineers</h3>
          <div id="editDesignerRows" class="engineer-grid"></div>
        </div>
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
  renderEngineerFields("editDesignerRows", d.designers || []);
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
    designers: collectDesigners("editDesignerRows"),
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
