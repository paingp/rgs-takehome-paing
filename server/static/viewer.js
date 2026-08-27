/* Sheet viewer and symbol selection.
 *
 * Drag a rough box to pull a symbol out of the ink, then count every instance of it on the
 * sheet. Detections come back already banded and already coloured -- the browser never
 * decides what counts, it only draws what takeoff.banding decided.
 *
 * All geometry crossing to the server is in the tile pyramid's IMAGE pixels. The server
 * converts to detection pixels through takeoff.spaces; the browser never assumes the two
 * are the same scale even though today they are.
 */

const els = {
  stage: document.getElementById("stage"),
  viewer: document.getElementById("viewer"),
  overlay: document.getElementById("overlay"),
  rubber: document.getElementById("rubber"),
  page: document.getElementById("page"),
  count: document.getElementById("count"),
  docSelect: document.getElementById("doc-select"),
  upload: document.getElementById("upload"),
  uploadInput: document.getElementById("upload-input"),
  prev: document.getElementById("prev"),
  next: document.getElementById("next"),
  readout: document.getElementById("readout"),
  selectMode: document.getElementById("select-mode"),
  candidatesToggle: document.getElementById("candidates-toggle"),
  preview: document.getElementById("preview"),
  panelHint: document.getElementById("panel-hint"),
  panelNote: document.getElementById("panel-note"),
  stats: document.getElementById("stats"),
  statSize: document.getElementById("stat-size"),
  statPx: document.getElementById("stat-px"),
  statParts: document.getElementById("stat-parts"),
  statInk: document.getElementById("stat-ink"),
  loading: document.getElementById("loading"),
  loadingMessage: document.getElementById("loading-message"),
  loadingBar: document.getElementById("loading-bar"),
  countButton: document.getElementById("count-button"),
  results: document.getElementById("results"),
  resultClass: document.getElementById("result-class"),
  resultTemplate: document.getElementById("result-template"),
  bands: document.getElementById("bands"),
  hits: document.getElementById("hits"),
  detectionsToggle: document.getElementById("detections-toggle"),
  fitSheet: document.getElementById("fit-sheet"),
  hitPrev: document.getElementById("hit-prev"),
  hitNext: document.getElementById("hit-next"),
  hitAccept: document.getElementById("hit-accept"),
  hitReject: document.getElementById("hit-reject"),
  hitPosition: document.getElementById("hit-position"),
  reset: document.getElementById("reset"),
  candidatesHint: document.getElementById("candidates-hint"),
  detail: document.getElementById("detail"),
  detailTitle: document.getElementById("detail-title"),
  detailCrop: document.getElementById("detail-crop"),
  detailFields: document.getElementById("detail-fields"),
};

const state = {
  viewer: null,
  page: 5,
  pageCount: 0,
  sourceDpi: 300,
  candidates: null,      // Float array of [x, y, w, h] in image px, or null if not fetched
  showCandidates: false,
  selectMode: false,
  shiftHeld: false,
  selectedBox: null,     // the snapped bbox, image px
  drag: null,
  detections: null,      // [{bbox_image_px, colour, status, ...}] or null
  highlighted: null,     // id of the hit the cursor is on
  cursor: -1,            // index into detections, for stepping through
  showDetections: true,
  verdicts: {},          // detection id -> "kept" | "dropped", this session only
  classes: {},           // id -> registry entry, so the UI knows which need a selection
  doc: null,             // document id, or null for the drawing the tool ships with
  documents: [],
};

/* Every page endpoint is scoped to a document. The bundled drawing is the default so the
 * URL stays clean, and any other is named explicitly -- including a scan, which the tool
 * treats as a first-class drawing rather than a lesser one. */
function api(path) {
  if (!state.doc) return path;
  return path + (path.includes("?") ? "&" : "?") + `doc=${encodeURIComponent(state.doc)}`;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);

function selecting() {
  return state.selectMode || state.shiftHeld;
}

/* ------------------------------------------------------------------ pyramid + page load */

function showLoading(message, fraction) {
  els.loading.hidden = false;
  els.loadingMessage.textContent = message;
  els.loadingBar.style.width = `${Math.round((fraction || 0) * 100)}%`;
}

async function ensureBuilt(page) {
  let info = await (await fetch(api(`/api/pages/${page}/status`))).json();
  if (info.state === "ready") return info;

  showLoading("Building tile pyramid…", 0);
  await fetch(api(`/api/pages/${page}/build`), { method: "POST" });

  for (;;) {
    await sleep(400);
    info = await (await fetch(api(`/api/pages/${page}/status`))).json();
    if (info.state === "ready") return info;
    if (info.state === "error") throw new Error(info.message);
    showLoading(`Building tile pyramid… ${info.message || ""}`, info.progress);
  }
}

async function load(page) {
  state.page = page;
  state.candidates = null;
  state.selectedBox = null;
  clearPanel();
  els.countButton.textContent = "Count these";
  els.page.value = page;
  history.replaceState(null, "", state.doc ? `?page=${page}&doc=${state.doc}` : `?page=${page}`);

  let info;
  try {
    info = await ensureBuilt(page);
  } catch (err) {
    showLoading(`Could not build page ${page}: ${err.message}`, 0);
    return;
  }
  state.sourceDpi = info.dpi || 300;

  if (state.viewer) state.viewer.destroy();
  state.viewer = OpenSeadragon({
    element: els.viewer,
    prefixUrl: "/static/vendor/openseadragon/images/",
    tileSources: info.dzi,
    showNavigator: true,
    navigatorPosition: "TOP_RIGHT",
    navigatorHeight: "13%",
    navigatorWidth: "13%",
    // The sheet should sit whole and centred on open, and still allow zooming past 1:1 so a
    // 28 px receptacle glyph is inspectable.
    homeFillsViewer: false,
    visibilityRatio: 1,
    constrainDuringPan: true,
    minZoomImageRatio: 0.85,
    maxZoomPixelRatio: 4,
    zoomPerScroll: 1.3,
    animationTime: 0.6,
    springStiffness: 8,
    gestureSettingsMouse: { clickToZoom: false, dblClickToZoom: true },
    showRotationControl: false,
    showFlipControl: false,
  });

  const viewer = state.viewer;
  viewer.addHandler("open", () => {
    els.loading.hidden = true;
    viewer.viewport.goHome(true);
    applyMouseNav();
    redraw();
  });
  viewer.addHandler("animation", redraw);
  viewer.addHandler("update-viewport", redraw);
  viewer.addHandler("resize", redraw);
  viewer.addHandler("open-failed", (e) =>
    showLoading(`Tile source failed: ${e.message || "unknown error"}`, 0)
  );
}

/* --------------------------------------------------------------------------- projection */

/* image px -> viewer element px, as a plain affine pair.
 *
 * Converting 4,770 rectangles one at a time through OpenSeadragon on every animation frame
 * is needless work: the mapping is a uniform scale plus a translation, so two reference
 * points recover it and the rest is arithmetic.
 */
function projection() {
  const viewer = state.viewer;
  if (!viewer || !viewer.world.getItemCount()) return null;
  const vp = viewer.viewport;
  const a = vp.viewportToViewerElementCoordinates(
    vp.imageToViewportCoordinates(new OpenSeadragon.Point(0, 0))
  );
  const b = vp.viewportToViewerElementCoordinates(
    vp.imageToViewportCoordinates(new OpenSeadragon.Point(1000, 1000))
  );
  return { scale: (b.x - a.x) / 1000, ox: a.x, oy: a.y };
}

function updateReadout() {
  const p = projection();
  if (!p) return;
  const effectiveDpi = p.scale * state.sourceDpi;
  const ratio = p.scale >= 1 ? `${p.scale.toFixed(1)} : 1` : `1 : ${(1 / p.scale).toFixed(1)}`;
  els.readout.textContent = `${ratio}  ·  ${Math.round(effectiveDpi)} DPI effective`;
}

/* ------------------------------------------------------------------------------ overlay */

function redraw() {
  updateReadout();

  const canvas = els.overlay;
  const rect = els.stage.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(rect.width * dpr) || canvas.height !== Math.round(rect.height * dpr)) {
    canvas.width = Math.round(rect.width * dpr);
    canvas.height = Math.round(rect.height * dpr);
    canvas.style.width = `${rect.width}px`;
    canvas.style.height = `${rect.height}px`;
  }

  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, rect.width, rect.height);

  const p = projection();
  if (!p) return;

  if (state.showCandidates && state.candidates) {
    ctx.strokeStyle = "rgba(230, 159, 0, 0.85)";   // Okabe-Ito amber: seen, not judged
    ctx.lineWidth = 1;
    ctx.beginPath();
    const boxes = state.candidates;
    for (let i = 0; i < boxes.length; i += 4) {
      const x = boxes[i] * p.scale + p.ox;
      const y = boxes[i + 1] * p.scale + p.oy;
      const w = boxes[i + 2] * p.scale;
      const h = boxes[i + 3] * p.scale;
      if (x + w < 0 || y + h < 0 || x > rect.width || y > rect.height) continue;
      ctx.rect(x, y, Math.max(w, 2), Math.max(h, 2));
    }
    ctx.stroke();
  }

  /* Detections carry their own band colour from the server, so the viewer cannot drift out
   * of step with takeoff.banding. A counted box and a review box differ only in colour --
   * both are drawn, because a review result that is hidden is a result that is lost. */
  if (state.detections && state.showDetections) {
    for (const d of state.detections) {
      const [bx, by, bw, bh] = d.bbox_image_px;
      const x = bx * p.scale + p.ox;
      const y = by * p.scale + p.oy;
      const w = Math.max(bw * p.scale, 3);
      const h = Math.max(bh * p.scale, 3);
      if (x + w < 0 || y + h < 0 || x > rect.width || y > rect.height) continue;

      const lead = d.id === state.highlighted;
      const verdict = state.verdicts[d.id];
      const pad = lead ? 4 : 2;

      /* A human verdict is drawn on top of the band colour rather than replacing it: the
       * detector's opinion and the reviewer's are different facts and both stay legible. */
      ctx.save();
      if (verdict === "dropped") {
        ctx.globalAlpha = 0.45;
        ctx.setLineDash([5, 3]);
      }
      ctx.strokeStyle = d.colour;
      ctx.lineWidth = lead ? 3 : 2;
      ctx.strokeRect(x - pad, y - pad, w + 2 * pad, h + 2 * pad);

      if (verdict === "kept") {
        ctx.fillStyle = d.colour;
        ctx.fillRect(x - pad, y - pad - 4, 8, 4);
      } else if (verdict === "dropped") {
        ctx.beginPath();
        ctx.moveTo(x - pad, y - pad);
        ctx.lineTo(x + w + pad, y + h + pad);
        ctx.stroke();
      }
      ctx.restore();

      // A ring on the highlighted hit, so clicking a row in the list is findable at any zoom.
      if (lead) {
        ctx.strokeStyle = d.colour;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(x + w / 2, y + h / 2, Math.max(w, h) * 1.6 + 14, 0, Math.PI * 2);
        ctx.stroke();
      }
    }
  }

  if (state.selectedBox) {
    const [bx, by, bw, bh] = state.selectedBox;
    ctx.strokeStyle = "#0072B2";
    ctx.lineWidth = 2;
    ctx.setLineDash([6, 4]);
    ctx.strokeRect(bx * p.scale + p.ox, by * p.scale + p.oy, bw * p.scale, bh * p.scale);
    ctx.setLineDash([]);
  }
}

async function setCandidates(on) {
  state.showCandidates = on;
  els.candidatesToggle.setAttribute("aria-pressed", String(on));
  els.candidatesHint.hidden = !on;
  if (on && !state.candidates) {
    els.candidatesToggle.textContent = "Loading…";
    try {
      const response = await fetch(api(`/api/pages/${state.page}/candidates`));
      if (!response.ok) {
        throw new Error(
          response.status === 404
            ? "no /candidates route — the server is running older code, restart uvicorn"
            : `${response.status} ${response.statusText}`
        );
      }
      const data = await response.json();
      state.candidates = Float64Array.from(data.boxes.flat());
      els.candidatesToggle.textContent = `Candidates (${data.count.toLocaleString()})`;
    } catch (err) {
      els.candidatesToggle.textContent = "Candidates";
      note(`Could not load candidates: ${err.message}`);
    }
  }
  redraw();
}

/* ---------------------------------------------------------------------- select and snap */

function applyMouseNav() {
  const active = selecting();
  if (state.viewer) state.viewer.setMouseNavEnabled(!active);
  els.stage.classList.toggle("selecting", active);
}

function setSelectMode(on) {
  state.selectMode = on;
  els.selectMode.setAttribute("aria-pressed", String(on));
  applyMouseNav();
}

function stagePoint(event) {
  const rect = els.stage.getBoundingClientRect();
  return {
    x: clamp(event.clientX - rect.left, 0, rect.width),
    y: clamp(event.clientY - rect.top, 0, rect.height),
  };
}

els.stage.addEventListener("pointerdown", (event) => {
  if (!selecting() || event.button !== 0) return;
  event.preventDefault();
  els.stage.setPointerCapture(event.pointerId);
  const p = stagePoint(event);
  state.drag = { x0: p.x, y0: p.y, x1: p.x, y1: p.y };
  drawRubber();
});

els.stage.addEventListener("pointermove", (event) => {
  if (!state.drag) return;
  const p = stagePoint(event);
  state.drag.x1 = p.x;
  state.drag.y1 = p.y;
  drawRubber();
});

els.stage.addEventListener("pointerup", async (event) => {
  if (!state.drag) return;
  const drag = state.drag;
  state.drag = null;
  els.rubber.hidden = true;
  els.stage.releasePointerCapture?.(event.pointerId);

  const w = Math.abs(drag.x1 - drag.x0);
  const h = Math.abs(drag.y1 - drag.y0);
  if (w < 4 || h < 4) return;   // a click, not a drag

  const p = projection();
  if (!p) return;
  const box = [
    (Math.min(drag.x0, drag.x1) - p.ox) / p.scale,
    (Math.min(drag.y0, drag.y1) - p.oy) / p.scale,
    w / p.scale,
    h / p.scale,
  ];
  await submitSelection(box);
});

function drawRubber() {
  const d = state.drag;
  if (!d) return;
  els.rubber.hidden = false;
  els.rubber.style.left = `${Math.min(d.x0, d.x1)}px`;
  els.rubber.style.top = `${Math.min(d.y0, d.y1)}px`;
  els.rubber.style.width = `${Math.abs(d.x1 - d.x0)}px`;
  els.rubber.style.height = `${Math.abs(d.y1 - d.y0)}px`;
}

function clearPanel() {
  state.selectedBox = null;
  syncCountButton();
  clearResults();
  els.preview.hidden = true;
  els.stats.hidden = true;
  els.panelNote.hidden = true;
  els.panelHint.hidden = false;
}

function note(text) {
  els.panelNote.textContent = text;
  els.panelNote.hidden = false;
}

async function submitSelection(bboxImagePx) {
  try {
    const response = await fetch(api(`/api/pages/${state.page}/select`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bbox_image_px: bboxImagePx }),
    });

    // A failed request must not read as an empty selection. A 404 here means the server is
    // running older code than this page -- uvicorn's reloader does not always catch an edit
    // -- and reporting that as "nothing found" sends you hunting for a symbol that is there.
    if (!response.ok) {
      state.selectedBox = null;
      clearPanel();
      note(
        response.status === 404
          ? "The server has no /select route — it is running older code. Restart uvicorn."
          : `Server returned ${response.status} ${response.statusText}.`
      );
      redraw();
      return;
    }

    const result = await response.json();

    if (!result.found) {
      state.selectedBox = null;
      clearPanel();
      note(result.reason || "Nothing found in that box.");
      redraw();
      return;
    }

    state.selectedBox = result.bbox_image_px;
    syncCountButton();
    els.panelHint.hidden = true;
    els.panelNote.hidden = true;
    els.preview.src = result.preview_png;
    els.preview.hidden = false;

    const [wIn, hIn] = result.size_in;
    const [wPx, hPx] = result.size_px;
    els.statSize.textContent = `${wIn.toFixed(3)} × ${hIn.toFixed(3)} in`;
    els.statPx.textContent = `${wPx} × ${hPx} px @ ${state.sourceDpi} DPI`;
    els.statParts.textContent = String(result.component_count);
    els.statInk.textContent = `${result.ink_px.toLocaleString()} px`;
    els.stats.hidden = false;
    redraw();
  } catch (err) {
    note(`Selection failed: ${err.message}`);
  }
}

/* ------------------------------------------------------------------------------ counting */

const BAND_LABEL = {
  counted: "Counted",
  review: "Needs review",
  rejected: "Rejected",
};
const BAND_COLOUR = {
  counted: "#0072B2",
  review: "#E69F00",
  rejected: "#999999",
};

function clearResults() {
  state.detections = null;
  state.highlighted = null;
  state.cursor = -1;
  state.verdicts = {};
  els.results.hidden = true;
  els.detectionsToggle.hidden = true;
  els.fitSheet.hidden = true;
  els.bands.replaceChildren();
  els.hits.replaceChildren();
  els.detail.hidden = true;
  cropCache.clear();
}

/* The registry is still read, but only so the panel can show what a selection was
 * recognised as. Nothing here chooses what gets counted -- the drag does. */
async function loadClasses() {
  const data = await (await fetch("/api/classes")).json();
  state.classes = Object.fromEntries(data.classes.map((c) => [c.id, c]));
  syncCountButton();
}

/* One gesture for every symbol: drag a box, press Count these. How a symbol gets counted is
 * measured from what was selected, not declared per class, so the button behaves the same
 * whatever is in the dropdown -- and an unseen symbol needs no new case here. */
function syncCountButton() {
  els.countButton.disabled = !state.selectedBox;
  els.countButton.title = state.selectedBox
    ? "Count every instance of the symbol you selected"
    : "Drag a box around one instance first";
}

/* Fly to a hit and flag it. The list is the index; the sheet is the document. */
function revealHit(index) {
  if (!state.detections || !state.detections.length) return;
  state.cursor = clamp(index, 0, state.detections.length - 1);
  const detection = state.detections[state.cursor];
  state.highlighted = detection.id;

  const [x, y, w, h] = detection.bbox_image_px;
  const viewer = state.viewer;
  if (viewer && viewer.world.getItemCount()) {
    const item = viewer.world.getItemAt(0);
    viewer.viewport.panTo(
      item.imageToViewportCoordinates(new OpenSeadragon.Point(x + w / 2, y + h / 2))
    );
    // Enough zoom that a 0.14 in glyph is legible, without slamming to maximum.
    const span = item.imageToViewportCoordinates(new OpenSeadragon.Point(Math.max(w, h) * 14, 0));
    const origin = item.imageToViewportCoordinates(new OpenSeadragon.Point(0, 0));
    viewer.viewport.zoomTo(1 / Math.max(span.x - origin.x, 1e-6));
  }
  syncReviewBar();
  redraw();
}

function stepHit(delta) {
  if (!state.detections || !state.detections.length) return;
  const n = state.detections.length;
  revealHit(state.cursor < 0 ? 0 : (state.cursor + delta + n) % n);
}

function fitSheet() {
  if (state.viewer && state.viewer.world.getItemCount()) state.viewer.viewport.goHome();
}

/* A verdict is the reviewer's, and it never rewrites the detector's band -- the two are
 * separate facts. Stable detection ids mean these survive a re-count of the same page. */
function judge(verdict) {
  if (!state.detections || state.cursor < 0) return;
  const detection = state.detections[state.cursor];
  state.verdicts[detection.id] =
    state.verdicts[detection.id] === verdict ? undefined : verdict;
  if (state.verdicts[detection.id] === undefined) delete state.verdicts[detection.id];
  syncReviewBar();
  redraw();
  if (state.verdicts[detection.id]) stepHit(+1);
}

/* One detection's full record. Crops are fetched on demand and memoised: the sheet has
 * thousands of candidates and embedding every preview in the count response would send a
 * lot of pixels nobody looks at. */
const cropCache = new Map();

async function cropFor(detection) {
  if (cropCache.has(detection.id)) return cropCache.get(detection.id);
  const [x, y, w, h] = detection.bbox_image_px;
  const query = new URLSearchParams({ x, y, w, h });
  try {
    const response = await fetch(api(`/api/pages/${state.page}/crop?${query}`));
    if (!response.ok) return null;
    const { png } = await response.json();
    cropCache.set(detection.id, png);
    return png;
  } catch {
    return null;
  }
}

function field(dl, term, value, className) {
  const dt = document.createElement("dt");
  dt.textContent = term;
  const dd = document.createElement("dd");
  if (className) dd.className = className;
  if (value instanceof Node) dd.append(value);
  else dd.textContent = value;
  dl.append(dt, dd);
}

/* A coverage number and a bar for it. `match` is min(forward, backward), so showing the two
 * halves is what tells a reviewer which way a near miss failed. */
function coverage(value) {
  const wrap = document.createElement("span");
  wrap.append(document.createTextNode(value.toFixed(3)));
  const bar = document.createElement("span");
  bar.className = "bar";
  bar.style.width = `${Math.round(value * 46)}px`;
  bar.style.opacity = 0.35 + 0.65 * value;
  wrap.append(bar);
  return wrap;
}

async function renderDetail(detection) {
  if (!detection) {
    els.detail.hidden = true;
    return;
  }
  els.detail.hidden = false;

  els.detailTitle.replaceChildren();
  const chip = document.createElement("span");
  chip.className = "chip";
  chip.style.background = detection.colour;
  chip.textContent = BAND_LABEL[detection.status] || detection.status;
  els.detailTitle.append(chip, document.createTextNode(detection.label || "no label nearby"));

  const dl = els.detailFields;
  dl.replaceChildren();

  if (detection.label) field(dl, "Label", detection.label, "strong");
  field(dl, "Match", coverage(detection.match), "strong");
  field(dl, "On template", coverage(detection.forward));
  field(dl, "Of template", coverage(detection.backward));
  field(dl, "Margin", detection.margin === null ? "not evaluated" : detection.margin.toFixed(3));
  field(dl, "Orientation", detection.variant);
  field(dl, "Size", `${detection.size_in[0].toFixed(3)} × ${detection.size_in[1].toFixed(3)} in`);
  field(dl, "Pixels", `${detection.size_px[0]} × ${detection.size_px[1]} px`);
  field(dl, "Ink", `${detection.ink_px.toLocaleString()} px  (fill ${detection.fill.toFixed(2)})`);
  field(dl, "Centre", `${detection.centre_in[0].toFixed(2)}, ${detection.centre_in[1].toFixed(2)} in`);
  if (detection.nearby_text && detection.nearby_text.length > 1) {
    field(dl, "Nearby", detection.nearby_text.join("  ·  "));
  }
  if (detection.reason) field(dl, "Note", detection.reason);
  const verdict = state.verdicts[detection.id];
  field(dl, "Review", verdict === "kept" ? "kept" : verdict === "dropped" ? "dropped" : "—");
  field(dl, "ID", detection.id);

  els.detailCrop.removeAttribute("src");
  const png = await cropFor(detection);
  if (png && state.highlighted === detection.id) els.detailCrop.src = png;
}

function syncReviewBar() {
  const list = state.detections || [];
  const seen = Object.keys(state.verdicts).length;
  const kept = Object.values(state.verdicts).filter((v) => v === "kept").length;
  const dropped = seen - kept;

  els.hitPosition.textContent = list.length
    ? `${state.cursor < 0 ? "-" : state.cursor + 1} / ${list.length}` +
      (seen ? `  ·  ${kept} kept, ${dropped} dropped` : "")
    : "no matches";

  const current = state.cursor < 0 ? null : list[state.cursor];
  const verdict = current ? state.verdicts[current.id] : undefined;
  els.hitAccept.setAttribute("aria-pressed", String(verdict === "kept"));
  els.hitReject.setAttribute("aria-pressed", String(verdict === "dropped"));
  for (const button of [els.hitPrev, els.hitNext, els.hitAccept, els.hitReject]) {
    button.disabled = !list.length;
  }

  for (const row of els.hits.children) {
    const mark = state.verdicts[row.dataset.id];
    row.style.background = row.dataset.id === state.highlighted ? "#22272e" : "";
    row.style.opacity = mark === "dropped" ? "0.45" : "1";
    row.querySelector(".verdict").textContent =
      mark === "kept" ? "✓" : mark === "dropped" ? "✗" : "";
  }

  renderDetail(current);
}

/* Back to the state a freshly loaded sheet is in -- selection, count, review and overlays
 * all cleared -- without rebuilding the viewer, which would re-fetch tiles and lose the
 * view you are looking at. The sheet position is deliberately kept: reset is for clearing
 * what the tool decided, not for losing your place on the drawing. */
function resetAll() {
  state.selectedBox = null;
  state.drag = null;
  clearPanel();
  setSelectMode(false);
  setCandidates(false);
  els.panelNote.hidden = true;
  els.rubber.hidden = true;
  els.countButton.disabled = true;
  els.countButton.textContent = "Count these";
  redraw();
}

function setDetectionsVisible(on) {
  state.showDetections = on;
  els.detectionsToggle.setAttribute("aria-pressed", String(on));
  redraw();
}

function renderResults(result) {
  state.detections = result.detections;
  state.highlighted = null;
  state.cursor = -1;
  state.verdicts = {};

  /* Hand the sheet back the moment there is something to review. Select mode disables
   * OpenSeadragon's mouse navigation, so leaving it on after a count froze the drawing --
   * results were drawn and the page could not be panned or zoomed to look at them. */
  setSelectMode(false);

  els.resultClass.replaceChildren();
  const name = document.createElement("b");
  name.textContent = result.class_name;
  els.resultClass.append(
    name,
    document.createTextNode(
      ` — ${result.counts.total} match${result.counts.total === 1 ? "" : "es"} on this sheet`
    )
  );
  if (result.identified_as) {
    const why = document.createElement("span");
    why.className = "muted";
    why.textContent = ` (${result.identified_as})`;
    els.resultClass.append(why);
  }
  if (!result.registered) {
    const warn = document.createElement("p");
    warn.className = "muted small";
    warn.textContent =
      "This symbol is not in the registry, so it is counted with default thresholds that " +
      "nobody has calibrated, and with no caption pattern. Treat the number as a first look.";
    els.resultClass.append(warn);
  }

  if (result.template.detector === "arc") {
    const [lo, hi] = result.template.width_band_ft;
    els.resultTemplate.textContent =
      `Your selection measured as ${result.template.reason || "a curve"}. ` +
      `Sweeping ${lo}–${hi} ft across the sheet.` +
      (result.diagnostics && result.diagnostics.note ? ` ${result.diagnostics.note}` : "");
    finishResults(result);
    return;
  }

  const [tw, th] = result.template.size_in;
  const parts = [
    `Template ${tw.toFixed(3)} × ${th.toFixed(3)} in from your ${result.template.source}, ` +
      `${result.template.variants} orientations, ${result.template.ink_px.toLocaleString()} ink px.`,
  ];
  /* Matching runs on one connected glyph. When the drag held more than that -- a sheet
   * reference beside a marker, say -- say so, because the ignored piece is usually the
   * thing the user thought they were counting by. */
  if (result.template.trimmed) {
    parts.push(
      `Matched on the largest connected glyph; ${result.template.context_blobs} separate ` +
        `piece(s) in your box (${result.template.context_ink_px.toLocaleString()} ink px) ` +
        `are labels, not part of the shape.`
    );
  }
  if (result.diagnostics && result.diagnostics.note) parts.push(result.diagnostics.note);
  els.resultTemplate.textContent = parts.join(" ");
  finishResults(result);
}

/* The band tally, the hit list, and the chrome that goes with them. Shared, because a swept
 * class and a template class differ only in the sentence above them. */
function finishResults(result) {
  els.bands.replaceChildren(
    ...Object.entries(result.counts.by_band).map(([status, n]) => {
      const li = document.createElement("li");
      const swatch = document.createElement("span");
      swatch.className = "swatch";
      swatch.style.background = BAND_COLOUR[status] || "#999";
      const label = document.createElement("span");
      label.textContent = BAND_LABEL[status] || status;
      const value = document.createElement("span");
      value.className = "n";
      value.textContent = n;
      li.append(swatch, label, value);
      return li;
    })
  );

  els.hits.replaceChildren(
    ...result.detections.map((d, index) => {
      const li = document.createElement("li");
      li.dataset.id = d.id;
      li.title = d.reason || `${d.variant} · matched at ${d.match.toFixed(3)}`;
      const dot = document.createElement("span");
      dot.className = "dot";
      dot.style.background = d.colour;
      const name = document.createElement("span");
      name.textContent = d.label || d.variant;
      if (d.label) name.className = "label-name";
      const score = document.createElement("span");
      score.className = "score";
      score.textContent = d.match.toFixed(3);
      const verdict = document.createElement("span");
      verdict.className = "verdict";
      li.append(dot, name, score, verdict);
      li.addEventListener("click", () => revealHit(index));
      return li;
    })
  );

  els.results.hidden = false;
  els.detectionsToggle.hidden = false;
  els.fitSheet.hidden = false;
  setDetectionsVisible(true);
  syncReviewBar();
  redraw();
}

async function countSelection() {
  if (!state.selectedBox) return;
  els.countButton.disabled = true;
  els.countButton.textContent = "Counting…";
  try {
    const response = await fetch(api(`/api/pages/${state.page}/count`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // No class is sent. What is being counted is whatever was selected; the server
      // recognises it if it is registered and counts it unnamed if it is not.
      body: JSON.stringify({ bbox_image_px: state.selectedBox }),
    });
    if (!response.ok) {
      clearResults();
      note(
        response.status === 404
          ? "No /count route — the server is running older code. Restart uvicorn."
          : `Server returned ${response.status} ${response.statusText}.`
      );
      return;
    }
    const result = await response.json();
    if (!result.found) {
      clearResults();
      note(result.reason || "Nothing to count.");
      return;
    }
    renderResults(result);
    if (result.counts.total === 0) {
      note(result.diagnostics?.note || "No instances of that symbol on this sheet.");
    }
  } catch (err) {
    note(`Count failed: ${err.message}`);
  } finally {
    els.countButton.textContent = "Count these";
    syncCountButton();
  }
}

/* -------------------------------------------------------------------------------- chrome */

/* Review shortcuts. Ignored while a form control has focus, so typing a page number does
 * not step through matches. */
function typingInAField(target) {
  return target && /^(INPUT|SELECT|TEXTAREA)$/.test(target.tagName);
}

window.addEventListener("keydown", (e) => {
  if (e.key === "Shift" && !state.shiftHeld) {
    state.shiftHeld = true;
    applyMouseNav();
  }
  if (e.ctrlKey || e.metaKey || e.altKey || typingInAField(e.target)) return;

  if (e.key === "Escape") {
    if (state.selectMode || state.drag) setSelectMode(false);
    else resetAll();
    return;
  }
  if (e.key.toLowerCase() === "c") {
    e.preventDefault();
    setCandidates(!state.showCandidates);
    return;
  }
  if (!state.detections) return;

  const key = e.key.toLowerCase();
  const actions = {
    n: () => stepHit(+1),
    arrowright: () => stepHit(+1),
    p: () => stepHit(-1),
    arrowleft: () => stepHit(-1),
    a: () => judge("kept"),
    r: () => judge("dropped"),
    f: fitSheet,
    d: () => setDetectionsVisible(!state.showDetections),
  };
  if (actions[key]) {
    e.preventDefault();
    actions[key]();
  }
});

window.addEventListener("keyup", (e) => {
  if (e.key === "Shift") {
    state.shiftHeld = false;
    applyMouseNav();
  }
});

window.addEventListener("resize", redraw);

function step(delta) {
  load(clamp(state.page + delta, 1, state.pageCount || 28));
}

/* --------------------------------------------------------------------------- documents */

async function loadDocuments(select) {
  const data = await (await fetch("/api/documents")).json();
  state.documents = data.documents;
  els.docSelect.replaceChildren(
    ...data.documents.map((d) => {
      const option = document.createElement("option");
      option.value = d.bundled ? "" : d.id;
      option.textContent = `${d.name}  (${d.kind}, ${d.pages} page${d.pages === 1 ? "" : "s"})`;
      return option;
    })
  );
  if (select !== undefined) els.docSelect.value = select;
  els.docSelect.disabled = data.documents.length < 2;
}

function currentDocument() {
  return state.documents.find((d) => (d.bundled ? "" : d.id) === (state.doc || "")) || null;
}

async function openDocument(id) {
  state.doc = id || null;
  resetAll();
  const meta = await (await fetch(api("/api/pages"))).json();
  state.pageCount = meta.count;
  els.page.max = meta.count;

  // A scan has no text layer, so nothing can read a caption off it. Say so once, rather
  // than letting every detection quietly arrive with no label and look like a bug.
  const doc = currentDocument();
  const note = doc && !doc.has_text_layer ? "  ·  no text layer, so no labels" : "";
  els.count.innerHTML =
    `of ${meta.count} · ${meta.pdf}` + (note ? `<span id="doc-note">${note}</span>` : "");
  await load(1);
}

async function sendUpload(file) {
  els.upload.disabled = true;
  els.upload.textContent = "Opening…";
  try {
    const response = await fetch(`/api/documents?name=${encodeURIComponent(file.name)}`, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: file,
    });
    const result = await response.json();
    if (!response.ok) {
      note(result.detail || `Upload failed (${response.status}).`);
      return;
    }
    await loadDocuments(result.bundled ? "" : result.id);
    await openDocument(result.bundled ? "" : result.id);
  } catch (err) {
    note(`Upload failed: ${err.message}`);
  } finally {
    els.upload.disabled = false;
    els.upload.textContent = "Open…";
  }
}

async function main() {
  await loadDocuments("");
  const meta = await (await fetch("/api/pages")).json();
  state.pageCount = meta.count;
  els.page.max = meta.count;
  els.count.textContent = `of ${meta.count} · ${meta.pdf}`;

  els.prev.addEventListener("click", () => step(-1));
  els.next.addEventListener("click", () => step(+1));
  els.page.addEventListener("change", () =>
    load(clamp(Number(els.page.value) || 1, 1, state.pageCount))
  );
  els.selectMode.addEventListener("click", () => setSelectMode(!state.selectMode));
  els.candidatesToggle.addEventListener("click", () => setCandidates(!state.showCandidates));
  els.countButton.addEventListener("click", countSelection);
  els.docSelect.addEventListener("change", () => openDocument(els.docSelect.value));
  els.upload.addEventListener("click", () => els.uploadInput.click());
  els.uploadInput.addEventListener("change", () => {
    const file = els.uploadInput.files && els.uploadInput.files[0];
    els.uploadInput.value = "";
    if (file) sendUpload(file);
  });
  els.detectionsToggle.addEventListener("click", () =>
    setDetectionsVisible(!state.showDetections)
  );
  els.fitSheet.addEventListener("click", fitSheet);
  els.reset.addEventListener("click", resetAll);
  els.hitPrev.addEventListener("click", () => stepHit(-1));
  els.hitNext.addEventListener("click", () => stepHit(+1));
  els.hitAccept.addEventListener("click", () => judge("kept"));
  els.hitReject.addEventListener("click", () => judge("dropped"));
  await loadClasses();

  const fromUrl = Number(new URLSearchParams(location.search).get("page"));
  await load(clamp(Number.isFinite(fromUrl) && fromUrl > 0 ? fromUrl : 5, 1, meta.count));
}

main();
