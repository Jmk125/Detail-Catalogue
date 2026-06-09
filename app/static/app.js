
let manifest = null;
let projectId = null;
let pageIndex = 0;
let boxes = [];
let selectedId = null;
let dragState = null;
let panState = null;
let zoom = 1.0;
let currentMergeTargetId = null;

const $ = (id) => document.getElementById(id);

$("uploadBtn").addEventListener("click", uploadPdf);
$("prevBtn").addEventListener("click", () => changePage(-1));
$("nextBtn").addEventListener("click", () => changePage(1));
$("fitBtn").addEventListener("click", fitToView);
$("actualBtn").addEventListener("click", () => setZoom(1.0));
$("addBoxBtn").addEventListener("click", addBox);
$("deleteBtn").addEventListener("click", deleteSelected);
$("approveBtn").addEventListener("click", approveSheet);
$("downloadBtn").addEventListener("click", downloadProject);

const dropZone = $("dropZone");
const fileInput = $("pdfFile");
const canvasWrap = $("canvasWrap");

dropZone.addEventListener("click", () => fileInput.click());
dropZone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropZone.classList.add("dragover");
});
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropZone.classList.remove("dragover");
  if (e.dataTransfer.files.length) {
    fileInput.files = e.dataTransfer.files;
    $("status").textContent = `Selected: ${fileInput.files[0].name}`;
  }
});
fileInput.addEventListener("change", () => {
  if (fileInput.files.length) $("status").textContent = `Selected: ${fileInput.files[0].name}`;
});

canvasWrap.addEventListener("wheel", onWheelZoom, { passive: false });
canvasWrap.addEventListener("contextmenu", (e) => e.preventDefault());
canvasWrap.addEventListener("mousedown", startPanMaybe);

document.addEventListener("keydown", (e) => {
  if (e.key === "Delete" && selectedId) {
    e.preventDefault();
    deleteSelected();
  }
});

async function uploadPdf() {
  const file = fileInput.files[0];
  if (!file) {
    alert("Choose a PDF first.");
    return;
  }

  $("status").textContent = "Uploading, rendering pages, and detecting boxes...";

  const form = new FormData();
  form.append("file", file);
  form.append("project_name", $("projectName").value || "");
  form.append("design_team", $("designTeam").value || "");

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
  $("status").textContent = `Loaded ${manifest.pages.length} pages.`;
  loadPage();
}

function loadPage() {
  const page = manifest.pages[pageIndex];
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

  img.src = `/data/projects/${projectId}/${page.image}`;
  $("sheetInfo").textContent = `${manifest.project_name || "Unnamed Project"} — Page ${page.page_number} of ${manifest.pages.length} — ${boxes.length} candidate boxes`;
  renderBoxList();
  loadDetails();
}

function changePage(delta) {
  if (!manifest) return;
  pageIndex = Math.max(0, Math.min(manifest.pages.length - 1, pageIndex + delta));
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

function onWheelZoom(e) {
  e.preventDefault();
  const factor = e.deltaY > 0 ? 0.88 : 1.12;
  setZoom(zoom * factor, e.clientX, e.clientY);
}

function startPanMaybe(e) {
  if (e.button !== 2) return; // right mouse only
  e.preventDefault();
  panState = {
    startX: e.clientX,
    startY: e.clientY,
    scrollLeft: canvasWrap.scrollLeft,
    scrollTop: canvasWrap.scrollTop,
  };
  canvasWrap.classList.add("panning");
  document.addEventListener("mousemove", onPan);
  document.addEventListener("mouseup", stopPan);
}

function onPan(e) {
  if (!panState) return;
  canvasWrap.scrollLeft = panState.scrollLeft - (e.clientX - panState.startX);
  canvasWrap.scrollTop = panState.scrollTop - (e.clientY - panState.startY);
}

function stopPan() {
  panState = null;
  canvasWrap.classList.remove("panning");
  document.removeEventListener("mousemove", onPan);
  document.removeEventListener("mouseup", stopPan);
}

function getResizeModeFromMouseEvent(e, boxElement) {
  const rect = boxElement.getBoundingClientRect();
  const x = e.clientX - rect.left;
  const y = e.clientY - rect.top;
  const edgePx = 10; // screen pixels, intentionally not scaled
  let mode = "";

  const nearLeft = x <= edgePx;
  const nearRight = x >= rect.width - edgePx;
  const nearTop = y <= edgePx;
  const nearBottom = y >= rect.height - edgePx;

  if (nearTop) mode += "n";
  if (nearBottom) mode += "s";
  if (nearLeft) mode += "w";
  if (nearRight) mode += "e";

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
    el.className = "crop-box" +
      (box.id === selectedId ? " selected" : "") +
      (box.id === currentMergeTargetId ? " merge-target" : "");
    el.style.left = `${box.x}px`;
    el.style.top = `${box.y}px`;
    el.style.width = `${box.w}px`;
    el.style.height = `${box.h}px`;
    el.style.borderWidth = `${borderPx}px`;
    el.dataset.id = box.id;
    el.style.cursor = "move";

    const label = document.createElement("div");
    label.className = "label";
    label.textContent = box.id;
    label.style.fontSize = `${12 / zoom}px`;
    label.style.top = `${-22 / zoom}px`;
    label.style.padding = `${2 / zoom}px ${5 / zoom}px`;
    el.appendChild(label);

    el.addEventListener("mousemove", (e) => {
      if (dragState) return;
      el.style.cursor = cursorForMode(getResizeModeFromMouseEvent(e, el));
    });

    el.addEventListener("mousedown", (e) => {
      if (e.button !== 0) return;
      e.stopPropagation();
      const mode = getResizeModeFromMouseEvent(e, el);
      startDrag(e, box.id, mode);
    });

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
    li.onclick = () => {
      selectedId = box.id;
      currentMergeTargetId = null;
      renderBoxes();
      renderBoxList();
    };
    list.appendChild(li);
  });
}

function startDrag(e, id, mode) {
  selectedId = id;
  const box = boxes.find(b => b.id === id);
  dragState = {
    mode,
    id,
    startX: e.clientX,
    startY: e.clientY,
    orig: { ...box },
    moved: false,
  };
  document.addEventListener("mousemove", onDrag);
  document.addEventListener("mouseup", stopDrag);
  renderBoxes();
  renderBoxList();
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

  if (mode === "move") {
    x = o.x + dx;
    y = o.y + dy;
  } else {
    if (mode.includes("w")) {
      x = o.x + dx;
      w = o.w - dx;
    }
    if (mode.includes("e")) w = o.w + dx;
    if (mode.includes("n")) {
      y = o.y + dy;
      h = o.h - dy;
    }
    if (mode.includes("s")) h = o.h + dy;

    if (w < minSize) {
      if (mode.includes("w")) x = o.x + o.w - minSize;
      w = minSize;
    }
    if (h < minSize) {
      if (mode.includes("n")) y = o.y + o.h - minSize;
      h = minSize;
    }
  }

  box.x = Math.max(0, x);
  box.y = Math.max(0, y);
  box.w = Math.max(minSize, w);
  box.h = Math.max(minSize, h);

  currentMergeTargetId = findMergeTarget(box.id);
  renderBoxes();
  renderBoxList();
}

function stopDrag() {
  if (dragState && dragState.moved) {
    applyOverlapMerge(dragState.id);
  }
  dragState = null;
  currentMergeTargetId = null;
  document.removeEventListener("mousemove", onDrag);
  document.removeEventListener("mouseup", stopDrag);
  renderBoxes();
  renderBoxList();
}

function boxArea(b) {
  return b.w * b.h;
}

function intersectionArea(a, b) {
  const x0 = Math.max(a.x, b.x);
  const y0 = Math.max(a.y, b.y);
  const x1 = Math.min(a.x + a.w, b.x + b.w);
  const y1 = Math.min(a.y + a.h, b.y + b.h);
  return Math.max(0, x1 - x0) * Math.max(0, y1 - y0);
}

function unionBox(a, b) {
  const x0 = Math.min(a.x, b.x);
  const y0 = Math.min(a.y, b.y);
  const x1 = Math.max(a.x + a.w, b.x + b.w);
  const y1 = Math.max(a.y + a.h, b.y + b.h);
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

function findMergeTarget(activeId) {
  const active = boxes.find(b => b.id === activeId);
  if (!active) return null;

  let best = null;
  let bestRatio = 0;

  for (const other of boxes) {
    if (other.id === activeId) continue;

    const smallerArea = Math.min(boxArea(active), boxArea(other));
    const ratio = intersectionArea(active, other) / Math.max(1, smallerArea);

    if (ratio >= 0.50 && ratio > bestRatio) {
      best = other;
      bestRatio = ratio;
    }
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
  keep.x = u.x;
  keep.y = u.y;
  keep.w = u.w;
  keep.h = u.h;
  keep.source = keep.source.includes("merged") ? keep.source : `${keep.source}+merged`;

  boxes = boxes.filter(b => b.id !== remove.id);
  selectedId = keep.id;
}

function addBox() {
  const wrap = $("canvasWrap");
  const margin = stageMarginPx();
  const id = `user_${Date.now()}`;
  boxes.push({
    id,
    x: ((wrap.scrollLeft - margin) / zoom) + 80,
    y: ((wrap.scrollTop - margin) / zoom) + 80,
    w: 500,
    h: 350,
    confidence: 1.0,
    source: "user"
  });
  selectedId = id;
  renderBoxes();
  renderBoxList();
}

function deleteSelected() {
  if (!selectedId) return;
  boxes = boxes.filter(b => b.id !== selectedId);
  selectedId = null;
  currentMergeTargetId = null;
  renderBoxes();
  renderBoxList();
}

async function approveSheet() {
  if (!projectId) return;

  const res = await fetch("/api/approve-sheet", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      project_id: projectId,
      page_index: pageIndex,
      boxes
    })
  });

  if (!res.ok) {
    alert(await res.text());
    return;
  }

  const data = await res.json();
  manifest.pages[pageIndex].boxes = structuredClone(boxes);
  manifest.pages[pageIndex].approved = true;
  await loadDetails();

  if (pageIndex < manifest.pages.length - 1) {
    pageIndex += 1;
    loadPage();
  } else {
    alert(`Saved ${data.saved} detail crops. Last sheet complete.`);
  }
}

async function loadDetails() {
  if (!projectId) return;
  const res = await fetch(`/api/projects/${projectId}/details`);
  const data = await res.json();

  const div = $("detailsList");
  div.innerHTML = "";

  const pageDetails = data.details.filter(d => d.page_index === pageIndex);
  if (pageDetails.length === 0) {
    div.textContent = "No approved details saved for this sheet yet.";
    return;
  }

  for (const d of pageDetails) {
    const card = document.createElement("div");
    card.className = "detail-card";
    card.innerHTML = `
      <strong>Page ${d.page_number}</strong><br>
      AI status: ${d.ai_status}<br>
      <img src="/data/projects/${projectId}/${d.crop_image}" />
    `;
    div.appendChild(card);
  }
}

function downloadProject() {
  if (!projectId) return;
  window.location.href = `/api/projects/${projectId}/download`;
}
