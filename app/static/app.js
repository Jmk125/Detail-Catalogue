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
let sheetBox = null;
let sheetDragState = null;

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
$("scanUnscannedBtn").addEventListener("click", scanUnscannedDetails);
$("gridToggleBtn").addEventListener("click", () => { libraryGrid = !libraryGrid; loadLibrary(); });
$("closeDetailBtn").addEventListener("click", () => $("detailModal").classList.add("hidden"));
$("closeNoteBtn").addEventListener("click", () => $("noteModal").classList.add("hidden"));
$("noteModal").addEventListener("click", (e) => { if (e.target === $("noteModal")) $("noteModal").classList.add("hidden"); });
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

  const parts = [];
  if (localUploading) parts.push(`${localUploading} file(s) uploading`);
  if (activePages) parts.push(`${activePages} sheet(s) rendering/detecting boxes`);
  if (activeAi) parts.push(`${activeAi} AI tagging job(s) pending/running`);
  if (localProcessing && !activePages) parts.push("starting sheet processing");
  $("backgroundBarText").textContent = parts.join(" • ");

  const completedPages = (pages.ready || 0) + (pages.approved || 0) + (pages.skipped || 0) + (pages.failed || 0);
  const totalPages = completedPages + activePages;
  const completedAi = ai.complete || 0;
  const totalAi = completedAi + activeAi + (ai.failed || 0);
  const uploadDone = uploadEntries.filter(e => e.status === "done" || e.status === "processing").length;
  const uploadTotal = uploadEntries.length;
  const done = completedPages + completedAi + uploadDone;
  const total = totalPages + totalAi + uploadTotal;
  const pct = total ? Math.max(4, Math.min(100, Math.round((done / total) * 100))) : 12;
  $("backgroundBarFill").style.width = `${pct}%`;
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

function showSheetLoadingOverlay() {
  $("sheetLoadingOverlay").classList.remove("hidden");
}

function hideSheetLoadingOverlay() {
  $("sheetLoadingOverlay").classList.add("hidden");
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
  currentMergeTargetId = null;

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
  await loadLibraryFacets();
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

function detailCard(d, compact = false, list = false) {
  const card = document.createElement("div");
  card.className = "detail-card" + (d.bookmarked ? " bookmarked" : "") + (list ? " list-card" : "");
  const hasNote = Boolean((d.notes || "").trim());
  const noteBadge = hasNote ? `<button class="note-badge" type="button" aria-label="View note" title="View note">📝</button>` : "";
  const thumb = `<div class="card-thumb-wrap"><img src="/data/projects/${d.project_id}/${d.thumbnail || d.crop_image}" alt="Detail thumbnail" /><button class="bookmark-badge" type="button" aria-label="Toggle bookmark">${d.bookmarked ? "★" : "☆"}</button>${noteBadge}</div>`;

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
        <small>${escapeHtml(d.discipline || "unknown")}${numbers ? ` · ${escapeHtml(numbers)}` : ""}</small>
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
      <small>${escapeHtml(d.discipline || "unknown")}</small>
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
