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
  editMode: document.getElementById("edit-mode"),
  truthToggle: document.getElementById("truth-toggle"),
  truthEdit: document.getElementById("truth-edit"),
  truthClass: document.getElementById("truth-class"),
  truthOccluded: document.getElementById("truth-occluded"),
  truthEditSize: document.getElementById("truth-edit-size"),
  truthDelete: document.getElementById("truth-delete"),
  truthUndo: document.getElementById("truth-undo"),
  truthUndoText: document.getElementById("truth-undo-text"),
  truthUndoButton: document.getElementById("truth-undo-button"),
  truthDeselect: document.getElementById("truth-deselect"),
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
  hitTally: document.getElementById("hit-tally"),
  reset: document.getElementById("reset"),
  candidatesHint: document.getElementById("candidates-hint"),
  detail: document.getElementById("detail"),
  truthAdd: document.getElementById("truth-add"),
  truthSave: document.getElementById("truth-save"),
  truthClear: document.getElementById("truth-clear"),
  truthLegend: document.getElementById("truth-legend"),
  gradeToggle: document.getElementById("grade-toggle"),
  gradeState: document.getElementById("grade-state"),
  gradeList: document.getElementById("grade-list"),
  truthState: document.getElementById("truth-state"),
  missedArm: document.getElementById("missed-arm"),
  missedConfirm: document.getElementById("missed-confirm"),
  missedClass: document.getElementById("missed-class"),
  missedOccluded: document.getElementById("missed-occluded"),
  missedSize: document.getElementById("missed-size"),
  missedCommit: document.getElementById("missed-commit"),
  missedCancel: document.getElementById("missed-cancel"),
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

  /* Ground truth for the page in view: what is really on the drawing, as opposed to what the
   * detector thinks. Held here so a person can build it up across several counts before
   * saving, and so `dirty` can warn before it is lost. */
  truth: [],             // [{uid, detId, class_id, bbox_image_px, label, occluded}]
  truthAnnotated: false, // has this page ever been annotated? empty != unseen
  // Classes a person has passed over on this page, whether or not they found any. A class
  // holding instances is reviewed by definition; this Set is how the OTHER claim gets made
  // -- "I looked for elevation markers on T4 and there are none" -- which is the difference
  // between a false positive the harness can report and a sheet it has to stay quiet about.
  reviewedClasses: new Set(),
  truthDirty: false,
  truthSavedPath: null,  // where the last save landed, shown on the status line
  truthUndo: null,       // {instances, dirty} held after Reset clears them, for one undo

  /* Correcting a recorded box. The detector's own box is never touched -- it is the tool's
   * claim and the evidence when it is wrong -- so what the handles move is the ANNOTATION of
   * it. This matters for grading: eval/harness.py matches within half the TRUTH box's larger
   * side, so a tighter box is a stricter target, not a cosmetic one. */
  editMode: false,
  activeTruth: null,     // uid of the instance showing handles
  resize: null,          // {uid, grip, box0} while a handle is being dragged

  /* Recorded boxes are off by default. On a fully annotated sheet they are forty-odd pink
   * rectangles over the drawing, which buries the thing being annotated -- and they are the
   * one overlay that persists through Reset, so leaving them on made Reset look inert. */
  showTruth: false,
  /* The last graded run of this page, as the server read it off disk. Never computed here:
   * grading is a full detection pass, and a second place where runs happen is a second set
   * of numbers to reconcile. */
  grade: null,
  showGrade: false,

  /* Recording a miss is a three-step gesture -- arm, drag, confirm -- because the box has to
   * be taken exactly as drawn. `missedMode` is armed and waiting; `pendingMissed` is a box
   * drawn and awaiting confirmation. */
  missedMode: false,
  pendingMissed: null,   // {bbox_image_px} the box drawn, before it is committed

  /* The current selection is the raw drag rather than a snapped component group: the drag
   * enclosed no symbol-sized ink. Kept selectable -- refusing the gesture outright is what
   * made a missed symbol impossible to point at -- but there is no template in it to count. */
  selectionIsRaw: false,
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
  if (!confirmDiscardTruth(page === state.page ? "Reloading this sheet" : `Opening page ${page}`)) {
    els.page.value = state.page;
    return;
  }
  state.page = page;
  state.candidates = null;
  state.selectedBox = null;
  // A half-finished miss belongs to the sheet it was drawn on, not the next one, and the
  // box being edited belongs to the truth list that is about to be replaced.
  state.missedMode = false;
  state.pendingMissed = null;
  state.activeTruth = null;
  state.resize = null;
  syncMissedUi();
  syncTruthEdit();
  clearPanel();
  els.countButton.textContent = "Count these";
  loadTruth();
  loadGrade();
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

  /* Recorded ground truth, one Okabe-Ito colour per class and never a band colour, so it can
   * never be mistaken for a detection. This is what is really on the drawing; the blue and
   * amber boxes are only what the tool thinks. */
  if (state.showTruth && state.truth && state.truth.length) {
    ctx.save();
    for (const t of state.truth) {
      const [bx, by, bw, bh] = t.bbox_image_px;
      const x = bx * p.scale + p.ox;
      const y = by * p.scale + p.oy;
      const w = Math.max(bw * p.scale, 3);
      const h = Math.max(bh * p.scale, 3);
      if (x + w < 0 || y + h < 0 || x > rect.width || y > rect.height) continue;
      // Occluded instances read heavier and longer-dashed. They are the handful the harness
      // reports on separately, so they have to be findable by eye while annotating.
      ctx.strokeStyle = truthColour(t.class_id);
      ctx.lineWidth = t.occluded ? 2.5 : 1.5;
      ctx.setLineDash(t.occluded ? [7, 3] : [2, 3]);
      ctx.strokeRect(x - 5, y - 5, w + 10, h + 10);
    }
    ctx.restore();

    /* Handles on the box being edited. Drawn on the STORED rectangle rather than the padded
     * one above, because that is the geometry they move and the geometry that gets saved. */
    const active = state.activeTruth ? truthById(state.activeTruth) : null;
    if (state.editMode && active) {
      const [bx, by, bw, bh] = active.bbox_image_px;
      ctx.save();
      ctx.strokeStyle = truthColour(active.class_id);
      ctx.lineWidth = 1.5;
      ctx.setLineDash([]);
      ctx.strokeRect(bx * p.scale + p.ox, by * p.scale + p.oy, bw * p.scale, bh * p.scale);
      ctx.fillStyle = "#1b1e22";
      for (const g of gripPoints(p, active.bbox_image_px)) {
        ctx.fillRect(g.x - GRIP_PX, g.y - GRIP_PX, GRIP_PX * 2, GRIP_PX * 2);
        ctx.strokeRect(g.x - GRIP_PX, g.y - GRIP_PX, GRIP_PX * 2, GRIP_PX * 2);
      }
      ctx.restore();
    }
  }

  /* The graded run. Drawn under nothing and over everything else: what it says is whether
   * the boxes already on screen were right, so it has to be readable against them. */
  if (state.showGrade && state.grade && state.grade.graded) {
    ctx.save();
    for (const [, row] of Object.entries(state.grade.classes || {})) {
      for (const box of row.boxes) {
        const [bx, by, bw, bh] = box.bbox_image_px;
        const x = bx * p.scale + p.ox;
        const y = by * p.scale + p.oy;
        const w = Math.max(bw * p.scale, 3);
        const h = Math.max(bh * p.scale, 3);
        if (x + w < 0 || y + h < 0 || x > rect.width || y > rect.height) continue;
        const style = GRADE_STYLES[box.kind];
        ctx.strokeStyle = style.colour;
        ctx.lineWidth = style.width;
        ctx.setLineDash(style.dash);
        ctx.strokeRect(x - 3, y - 3, w + 6, h + 6);
      }
    }
    ctx.restore();
  }

  /* A miss drawn but not yet confirmed. Solid, so it reads as live rather than recorded. */
  if (state.pendingMissed) {
    const [bx, by, bw, bh] = state.pendingMissed.bbox_image_px;
    ctx.save();
    ctx.strokeStyle = "#CC79A7";
    ctx.lineWidth = 2;
    ctx.setLineDash([]);
    ctx.strokeRect(bx * p.scale + p.ox, by * p.scale + p.oy, bw * p.scale, bh * p.scale);
    ctx.restore();
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
  // Edit mode takes the mouse for the same reason select mode does: a drag has to mean
  // "move this handle", not "pan the sheet".
  const active = selecting() || state.editMode;
  if (state.viewer) state.viewer.setMouseNavEnabled(!active);
  els.stage.classList.toggle("selecting", selecting());
  els.stage.classList.toggle("editing", state.editMode);
}

function setSelectMode(on) {
  state.selectMode = on;
  els.selectMode.setAttribute("aria-pressed", String(on));
  // The two modes both own the mouse, so they cannot both be on.
  if (on && state.editMode) setEditMode(false);
  applyMouseNav();
}

function setShowTruth(on) {
  state.showTruth = on;
  els.truthToggle.setAttribute("aria-pressed", String(on));
  // Nothing can be selected that is not on screen, so hiding the boxes ends any edit.
  if (!on) {
    state.activeTruth = null;
    state.resize = null;
    if (state.editMode) setEditMode(false);
  }
  syncTruthEdit();
  syncTruthLegend();
  redraw();
}

function setEditMode(on) {
  state.editMode = on;
  els.editMode.setAttribute("aria-pressed", String(on));
  if (on && state.selectMode) setSelectMode(false);
  // Editing invisible boxes is not a thing: turning it on turns the overlay on with it.
  if (on && !state.showTruth) setShowTruth(true);
  if (!on) {
    state.activeTruth = null;
    state.resize = null;
  }
  syncTruthEdit();
  applyMouseNav();
  note(
    on
      ? "Edit mode: click a recorded box, then drag a handle to resize it. Del removes it."
      : ""
  );
  if (!on) {
    els.panelNote.hidden = true;
    els.stage.style.cursor = "";
  }
  redraw();
}

/* ------------------------------------------------------------------- editing a truth box */

/* Screen (stage CSS px) -> image px. The forward direction is inlined all over redraw();
 * this is its inverse, and the only correct way to place a handle: what redraw() strokes is
 * padded by a constant number of SCREEN pixels and floored to a minimum size, so the drawn
 * rectangle is not the stored one and picking against it would be wrong at most zooms. */
function screenToImage(p, x, y) {
  return { x: (x - p.ox) / p.scale, y: (y - p.oy) / p.scale };
}

const GRIP_CURSORS = {
  nw: "nwse-resize", se: "nwse-resize",
  ne: "nesw-resize", sw: "nesw-resize",
  n: "ns-resize", s: "ns-resize",
  w: "ew-resize", e: "ew-resize",
};

const GRIP_PX = 5;        // half-width of a handle, in screen px
const GRIP_PICK_PX = 9;   // how close the pointer has to be to grab one
const MIN_BOX_PX = 6;     // an instance smaller than this in image px is a mis-drag

/* The eight handles, as unit positions on the box. */
const GRIPS = [
  ["nw", 0, 0], ["n", 0.5, 0], ["ne", 1, 0],
  ["w", 0, 0.5], ["e", 1, 0.5],
  ["sw", 0, 1], ["s", 0.5, 1], ["se", 1, 1],
];

function gripPoints(p, box) {
  const [bx, by, bw, bh] = box;
  return GRIPS.map(([name, fx, fy]) => ({
    name,
    x: (bx + bw * fx) * p.scale + p.ox,
    y: (by + bh * fy) * p.scale + p.oy,
  }));
}

/* Which handle of the active box is under the pointer, if any.
 *
 * NEAREST wins, not first-found, and the pick radius shrinks with the box. A door box is
 * about 16 x 22 screen px with the whole sheet in view, so at a fixed 9 px radius all eight
 * regions overlap and the first one in the list swallows every press -- grabbing the bottom
 * right corner would resize the bottom edge. Zooming in restores the full radius. */
function gripAt(p, sx, sy) {
  const active = state.activeTruth ? truthById(state.activeTruth) : null;
  if (!active) return null;
  const [, , bw, bh] = active.bbox_image_px;
  const shortest = Math.min(bw, bh) * p.scale;
  const reach = Math.max(3, Math.min(GRIP_PICK_PX, shortest / 3));

  let best = null;
  for (const g of gripPoints(p, active.bbox_image_px)) {
    const d = Math.hypot(g.x - sx, g.y - sy);
    if (d <= reach && (best === null || d < best.d)) best = { name: g.name, d };
  }
  return best && best.name;
}

/* Which recorded instance is under the pointer. Smallest first, so a box inside another is
 * still reachable. Picking is done against the STORED geometry, not the padded drawn one. */
function truthAt(p, sx, sy) {
  const here = screenToImage(p, sx, sy);
  const hits = state.truth.filter((t) => {
    const [x, y, w, h] = t.bbox_image_px;
    return here.x >= x && here.x <= x + w && here.y >= y && here.y <= y + h;
  });
  if (!hits.length) return null;
  hits.sort((a, b) => a.bbox_image_px[2] * a.bbox_image_px[3] - b.bbox_image_px[2] * b.bbox_image_px[3]);
  return hits[0];
}

/* Apply a handle drag. Corners move two edges, edge handles move one. The box is re-derived
 * from its edges and normalised, so dragging a handle past the opposite side flips it rather
 * than producing a negative width. */
function resizedBox(box0, grip, dx, dy) {
  let [x, y, w, h] = box0;
  let x0 = x, y0 = y, x1 = x + w, y1 = y + h;
  if (grip.includes("w")) x0 += dx;
  if (grip.includes("e")) x1 += dx;
  if (grip.includes("n")) y0 += dy;
  if (grip.includes("s")) y1 += dy;
  const nx = Math.min(x0, x1);
  const ny = Math.min(y0, y1);
  return [nx, ny, Math.max(Math.abs(x1 - x0), MIN_BOX_PX), Math.max(Math.abs(y1 - y0), MIN_BOX_PX)];
}

/* The inspector for the selected annotation.
 *
 * Class and occlusion are editable here for the same reason the geometry is: they are a
 * reviewer's judgement, and a judgement made while annotating forty doors is exactly the
 * kind that gets revised. Saving posts the whole page, so correcting an instance that is
 * already on disk needs nothing beyond editing it and pressing S again. */
function syncTruthEdit() {
  // Gated on SELECTION, not on edit mode. Recording a miss selects the new instance without
  // entering edit mode, and gating this on the mode meant the one moment you most want to
  // say "and something crosses it" was the one moment the checkbox was not on screen.
  // Edit mode is only about picking and resizing on the canvas.
  const active = state.activeTruth ? truthById(state.activeTruth) : null;
  els.truthEdit.hidden = !active;
  if (!active) return;

  const registered = Object.values(state.classes || {});
  const options = registered.map((c) => ({ id: c.id, name: c.name || c.id }));
  // An instance can carry a class the registry does not have -- "unknown" from a miss
  // recorded before any count. Keep it selectable rather than silently reassigning it.
  if (!options.some((o) => o.id === active.class_id)) {
    options.unshift({ id: active.class_id, name: `${active.class_id} (not registered)` });
  }
  els.truthClass.replaceChildren();
  for (const o of options) {
    const option = document.createElement("option");
    option.value = o.id;
    option.textContent = o.name;
    option.selected = o.id === active.class_id;
    els.truthClass.appendChild(option);
  }

  els.truthOccluded.checked = Boolean(active.occluded);
  const [, , w, h] = active.bbox_image_px;
  // Only the positive case is stated. Provenance is not persisted, so an instance read back
  // from disk has no link until a count relinks it -- calling that "recorded by hand" would
  // be a guess, and a wrong one for every accepted detection on a reloaded page.
  els.truthEditSize.textContent =
    `${Math.round(w)} × ${Math.round(h)} px at ${state.sourceDpi} DPI` +
    (active.detId ? " · linked to a detection" : "") +
    (state.editMode ? "" : " · E to resize");
}

function setActiveTruthClass(classId) {
  const active = state.activeTruth ? truthById(state.activeTruth) : null;
  if (!active || active.class_id === classId) return;
  active.class_id = classId;
  markTruthDirty(true);
  note(`Reclassified as ${classId}.`);
  syncTruthEdit();
  redraw();
}

function setActiveTruthOccluded(occluded) {
  const active = state.activeTruth ? truthById(state.activeTruth) : null;
  if (!active || Boolean(active.occluded) === occluded) return;
  active.occluded = occluded;
  markTruthDirty(true);
  note(occluded ? "Marked occluded — something crosses this instance." : "No longer occluded.");
  syncTruthEdit();
  redraw();
}

/* Remove every annotation on the page. This is the destructive one, so it is its own button
 * rather than a side effect of Reset -- and it is reversible, because 40 instances is an hour
 * of work. Nothing is deleted from disk; the saved file stands until the next save. */
function clearAnnotations() {
  if (!state.truth.length) {
    note("Nothing to clear — no annotations on this page.");
    return;
  }
  state.truthUndo = { instances: state.truth, dirty: state.truthDirty };
  const n = state.truth.length;
  state.truth = [];
  state.activeTruth = null;
  state.resize = null;
  syncTruthEdit();
  markTruthDirty(true);
  // The undo bar is the message for this action. Leaving the previous note up alongside it
  // reads as the current state and is a sentence about something that already happened.
  els.panelNote.hidden = true;
  showTruthUndo(
    `Cleared ${n} annotation${n === 1 ? "" : "s"} from this page. ` +
    `The saved file is untouched until you save again.`
  );
  redraw();
}

/* The one-step way back from a destructive clear. Held only in memory and only until the
 * next thing that changes the annotation set, which is enough: the danger is the accidental
 * press, and that is noticed immediately. */
function showTruthUndo(text) {
  els.truthUndoText.textContent = text;
  els.truthUndo.hidden = false;
}

function hideTruthUndo() {
  state.truthUndo = null;
  els.truthUndo.hidden = true;
}

function undoTruthClear() {
  if (!state.truthUndo) return;
  state.truth = state.truthUndo.instances;
  const dirty = state.truthUndo.dirty;
  hideTruthUndo();
  markTruthDirty(dirty);
  if (!state.showTruth) setShowTruth(true);
  note(`Restored ${state.truth.length} annotation${state.truth.length === 1 ? "" : "s"}.`);
  redraw();
}

function deleteActiveTruth() {
  hideTruthUndo();
  const active = state.activeTruth ? truthById(state.activeTruth) : null;
  if (!active) return;
  state.truth = state.truth.filter((t) => t !== active);
  // A recorded instance that came from a detection also loses its accept verdict, or the
  // review bar would go on showing a tick for something no longer recorded.
  if (active.detId) delete state.verdicts[active.detId];
  state.activeTruth = null;
  markTruthDirty(true);
  syncTruthEdit();
  syncReviewBar();
  note(`Removed. ${state.truth.length} instance${state.truth.length === 1 ? "" : "s"} recorded.`);
  redraw();
}

function stagePoint(event) {
  const rect = els.stage.getBoundingClientRect();
  return {
    x: clamp(event.clientX - rect.left, 0, rect.width),
    y: clamp(event.clientY - rect.top, 0, rect.height),
  };
}

els.stage.addEventListener("pointerdown", (event) => {
  if (state.editMode && event.button === 0) {
    event.preventDefault();
    const p = projection();
    if (!p) return;
    const s = stagePoint(event);
    const grip = gripAt(p, s.x, s.y);
    if (grip) {
      // On a handle: start resizing the box it belongs to. State first, capture second --
      // setPointerCapture throws for a pointer id the browser does not consider active, and
      // losing the whole gesture to that is worse than tracking without capture.
      const active = truthById(state.activeTruth);
      state.resize = { uid: active.uid, grip, box0: active.bbox_image_px.slice(), from: s };
      try {
        els.stage.setPointerCapture(event.pointerId);
      } catch {
        /* pointermove on the stage still tracks the drag */
      }
    } else {
      // Otherwise pick whichever recorded box was clicked, or clear the selection.
      const hit = truthAt(p, s.x, s.y);
      state.activeTruth = hit ? hit.uid : null;
      syncTruthEdit();
      redraw();
    }
    return;
  }
  if (!selecting() || event.button !== 0) return;
  event.preventDefault();
  els.stage.setPointerCapture(event.pointerId);
  const p = stagePoint(event);
  state.drag = { x0: p.x, y0: p.y, x1: p.x, y1: p.y };
  drawRubber();
});

els.stage.addEventListener("pointermove", (event) => {
  if (state.editMode) {
    const p = projection();
    if (!p) return;
    const s = stagePoint(event);
    if (state.resize) {
      const instance = truthById(state.resize.uid);
      if (instance) {
        const dx = (s.x - state.resize.from.x) / p.scale;
        const dy = (s.y - state.resize.from.y) / p.scale;
        instance.bbox_image_px = resizedBox(state.resize.box0, state.resize.grip, dx, dy);
        syncTruthEdit();
        redraw();
      }
      return;
    }
    // Hover feedback: the cursor says whether a press would resize, pick, or clear.
    const grip = gripAt(p, s.x, s.y);
    const cursor = grip ? GRIP_CURSORS[grip] : truthAt(p, s.x, s.y) ? "pointer" : "default";
    if (els.stage.style.cursor !== cursor) els.stage.style.cursor = cursor;
    return;
  }
  if (!state.drag) return;
  const p = stagePoint(event);
  state.drag.x1 = p.x;
  state.drag.y1 = p.y;
  drawRubber();
});

els.stage.addEventListener("pointerup", async (event) => {
  if (state.resize) {
    const instance = truthById(state.resize.uid);
    state.resize = null;
    // Release cannot be allowed to throw past this point: it does so whenever capture was
    // never granted, and an exception here would skip marking the edit unsaved -- the box
    // would be corrected on screen and quietly missing from the next save.
    try {
      els.stage.releasePointerCapture?.(event.pointerId);
    } catch {
      /* capture was never held */
    }
    if (instance) {
      const [, , w, h] = instance.bbox_image_px;
      markTruthDirty(true);
      note(`Box is now ${Math.round(w)} × ${Math.round(h)} px.`);
    }
    redraw();
    return;
  }
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
  // Armed for a miss: the box is the annotation, taken as drawn. It deliberately does NOT go
  // through /select -- there is no candidate to snap to, which is the whole point.
  if (state.missedMode) {
    capturePendingMissed(box);
    return;
  }
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
  state.selectionIsRaw = false;
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
      // The box stands even though nothing snapped to it. Discarding it here is what made a
      // missed symbol impossible to point at: the detector has no candidate where it failed,
      // so /select finds nothing, so the box vanished, so "+ Missed" stayed disabled --
      // exactly the case the annotation exists for. There is no ink to build a template
      // from, so counting is off; annotating is not.
      clearPanel();
      state.selectedBox = bboxImagePx;
      state.selectionIsRaw = true;
      syncCountButton();
      note(
        `${result.reason || "Nothing found in that box"} — the box is still selected, so it can be recorded as a missed instance.`
      );
      redraw();
      return;
    }

    state.selectedBox = result.bbox_image_px;
    state.selectionIsRaw = false;
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
  // A raw box holds no symbol-sized ink, so there is no template to count with -- but it is
  // still a legitimate selection to annotate.
  const countable = Boolean(state.selectedBox) && !state.selectionIsRaw;
  els.countButton.disabled = !countable;
  els.countButton.title = countable
    ? "Count every instance of the symbol you selected"
    : state.selectionIsRaw
      ? "No symbol-sized ink in that box — nothing to build a template from"
      : "Drag a box around one instance first";

  // "+ Missed" is the ENTRY POINT to the gesture now, not an action on an existing
  // selection, so it is never disabled.
  if (els.truthAdd) els.truthAdd.disabled = false;
}

/* Put a box in the middle of the screen, close enough to read. Every list in the panel is
 * an index into the drawing, so they all end up here. */
function flyTo(box) {
  const [x, y, w, h] = box;
  const viewer = state.viewer;
  if (!viewer || !viewer.world.getItemCount()) return;
  const item = viewer.world.getItemAt(0);
  viewer.viewport.panTo(
    item.imageToViewportCoordinates(new OpenSeadragon.Point(x + w / 2, y + h / 2))
  );
  // Enough zoom that a 0.14 in glyph is legible, without slamming to maximum.
  const span = item.imageToViewportCoordinates(new OpenSeadragon.Point(Math.max(w, h) * 14, 0));
  const origin = item.imageToViewportCoordinates(new OpenSeadragon.Point(0, 0));
  viewer.viewport.zoomTo(1 / Math.max(span.x - origin.x, 1e-6));
  redraw();
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
  truthFromVerdict(detection, state.verdicts[detection.id]);
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
    ? `${state.cursor < 0 ? "-" : state.cursor + 1} / ${list.length}`
    : "no matches";
  els.hitTally.textContent = list.length && seen ? `${kept} kept, ${dropped} dropped` : "";

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
  // Reset HIDES the annotations rather than clearing them. It clears what the tool decided,
  // and the overlay is part of that; the annotations themselves are what the person decided,
  // and removing them is a separate, deliberate button. Reset also runs from Escape twice,
  // which is far too easy a way to lose an hour of work.
  if (state.showTruth) {
    const n = state.truth.length;
    setShowTruth(false);
    if (n) {
      note(`Hid ${n} annotation${n === 1 ? "" : "s"}. G brings them back; nothing was removed.`);
    }
  }
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
  // Saved instances carry no link to a detection. Adopt them now, so a box corrected in an
  // earlier session is recognised as already recorded rather than accepted a second time.
  relinkTruthToDetections();

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


/* --------------------------------------------------------------------------- grading */

/* Four things a run can say about a box, and each has to be tellable from the others at a
 * glance while zoomed out. Green found it; vermillion is wrong either way round -- a claim
 * on ink that is not the symbol, or a symbol the tool never claimed -- and the two are
 * separated by fill rather than hue, because they are the same failure to a reader scanning
 * a sheet. Amber is the review band, the same amber banding.py already uses for it. */
const GRADE_STYLES = {
  matched: { colour: "#009E73", width: 1.5, dash: [], what: "found" },
  spurious: { colour: "#D55E00", width: 2.5, dash: [], what: "false positive" },
  missed: { colour: "#D55E00", width: 2.5, dash: [7, 4], what: "missed" },
  in_review: { colour: "#E69F00", width: 2, dash: [2, 4], what: "in review" },
};

// Matched boxes are drawn but not listed. There are 41 of them on T5 and they are the case
// nobody needs to look at; the list is for what went wrong.
const GRADE_LISTED = ["spurious", "missed", "in_review"];

async function loadGrade() {
  state.grade = null;
  try {
    const response = await fetch(api(`/api/pages/${state.page}/grade`));
    if (response.ok) state.grade = await response.json();
  } catch {
    /* an ungraded page is the normal case, not an error */
  }
  syncGrade();
  redraw();
}

function setShowGrade(on) {
  if (on && !(state.grade && state.grade.graded)) {
    note("This page has not been graded yet. Run `python -m eval.suites --page " +
         `${state.page}\` first.`);
    return;
  }
  state.showGrade = on;
  els.gradeToggle.setAttribute("aria-pressed", String(on));
  syncGrade();
  redraw();
}

/* When the run happened, in the reader's own terms. A report is read off disk, so it can be
 * older than the code that is being judged by it -- and the only defence against quoting a
 * stale number is saying plainly how old it is. */
function gradeAge(iso) {
  const then = Date.parse(iso);
  if (!Number.isFinite(then)) return iso;
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}

function syncGrade() {
  els.gradeList.replaceChildren();
  const run = state.grade;
  if (!run || !run.graded) {
    els.gradeToggle.disabled = true;
    els.gradeState.textContent = "Not graded yet — " + ((run && run.how) || "run eval.suites");
    return;
  }
  els.gradeToggle.disabled = false;
  els.gradeState.textContent = `Graded ${gradeAge(run.run_at)} against ${run.source}.`;
  if (!state.showGrade) return;

  for (const [classId, row] of Object.entries(run.classes || {})) {
    const head = document.createElement("li");
    head.className = "head";
    const name = (state.classes[classId] || {}).name || classId;
    head.textContent =
      `${name}: ${row.true_positives} found, ${row.false_positives} wrong, ` +
      `${row.false_negatives} missed, ${row.review_volume} in review`;
    els.gradeList.appendChild(head);

    for (const box of row.boxes) {
      if (!GRADE_LISTED.includes(box.kind)) continue;
      const style = GRADE_STYLES[box.kind];
      const li = document.createElement("li");
      li.className = "box";
      li.style.color = style.colour;
      const dot = document.createElement("span");
      dot.className = "dot";
      const what = document.createElement("span");
      what.className = "what";
      what.textContent = style.what + (box.occluded ? ", occluded" : "");
      const n = document.createElement("span");
      n.className = "n";
      n.textContent = box.match !== undefined ? box.match.toFixed(3) : (box.label || "");
      li.append(dot, what, n);
      li.title = box.reason || box.label || "";
      li.addEventListener("click", () => flyTo(box.bbox_image_px));
      els.gradeList.appendChild(li);
    }
  }
  for (const row of run.not_graded || []) {
    const li = document.createElement("li");
    li.className = "head";
    li.textContent = row;
    els.gradeList.appendChild(li);
  }
}

/* ------------------------------------------------------------------- annotating truth */

/* Two halves, and only one of them existed before. Accepting a detection records that it is
 * real; but a page's truth also has to record what the detector NEVER PROPOSED, because
 * false negatives are most of what occlusion work needs to measure and no accept/reject
 * gesture can ever produce one. `+ Missed` is that half: drag a box round something the tool
 * failed to find, and it becomes ground truth on its own. */

function truthKey(box) {
  return box.map((v) => Math.round(v)).join(",");
}

/* Ground truth is drawn one colour per class.
 *
 * It used to be one pink for everything, which was fine while a page held a single class and
 * wrong the moment it held two: a door and an elevation marker recorded on the same sheet
 * were indistinguishable, and the whole point of the overlay is to check what is recorded as
 * what. Okabe-Ito throughout, and deliberately not the three band colours a DETECTION uses --
 * truth and the detector's opinion must never be confusable. Assignment follows registry
 * order, so a class keeps its colour as long as the registry does. */
const TRUTH_COLOURS = ["#CC79A7", "#F0E442", "#56B4E9", "#D55E00", "#009E73"];
const TRUTH_COLOUR_UNKNOWN = "#BBBBBB";

function truthColour(classId) {
  const ids = Object.keys(state.classes || {});
  const i = ids.indexOf(classId);
  return i < 0 ? TRUTH_COLOUR_UNKNOWN : TRUTH_COLOURS[i % TRUTH_COLOURS.length];
}

/* What is recorded on this page, by class, in registry order. Drives the legend. */
function truthTally() {
  const counts = new Map();
  for (const t of state.truth) counts.set(t.class_id, (counts.get(t.class_id) || 0) + 1);
  const ids = Object.keys(state.classes || {});
  const ordered = [...counts.keys()].sort((a, b) => {
    const ia = ids.indexOf(a), ib = ids.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });
  return ordered.map((id) => ({
    id,
    name: (state.classes[id] && state.classes[id].name) || id,
    count: counts.get(id),
    colour: truthColour(id),
  }));
}

/* One row per registered class: how many are recorded, or a box to say there are none.
 *
 * It used to list only the classes with instances, which reads as a legend and hides the
 * question that matters while annotating -- have I looked for this yet? A blank space cannot
 * distinguish "no markers on this sheet" from "I never checked", and grading needs that
 * distinction to know whether a detection here is a false positive or nothing at all. */
function syncTruthLegend() {
  els.truthLegend.replaceChildren();
  if (!state.showTruth) {
    els.truthLegend.hidden = true;
    return;
  }
  const counts = new Map(truthTally().map((r) => [r.id, r]));
  const ids = Object.keys(state.classes || {});
  for (const id of [...counts.keys()].filter((k) => !ids.includes(k))) ids.push(id);

  for (const id of ids) {
    const row = counts.get(id);
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = (state.classes[id] && state.classes[id].name) || id;

    if (row) {
      li.style.color = row.colour;
      const swatch = document.createElement("span");
      swatch.className = "swatch";
      const n = document.createElement("span");
      n.className = "n";
      n.textContent = String(row.count);
      li.append(swatch, name, n);
    } else {
      // Nothing recorded. Either nobody has looked, or the sheet genuinely has none of it,
      // and only a person can say which.
      const label = document.createElement("label");
      label.className = "none-here";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = state.reviewedClasses.has(id);
      box.addEventListener("change", () => setClassReviewed(id, box.checked));
      const text = document.createElement("span");
      text.textContent = "none on this sheet";
      label.append(box, text);
      li.append(name, label);
    }
    els.truthLegend.appendChild(li);
  }
  els.truthLegend.hidden = false;
}

/* Asserting a zero is an annotation like any other: it makes the page dirty and has to be
 * saved. Ticking it for a class that has instances would be meaningless, so the box only
 * exists while the count is zero. */
function setClassReviewed(classId, reviewed) {
  if (reviewed) state.reviewedClasses.add(classId);
  else state.reviewedClasses.delete(classId);
  markTruthDirty(true);
  note(
    reviewed
      ? `Recorded: no ${(state.classes[classId] || {}).name || classId} on this sheet.`
      : `No longer claiming this sheet is free of ${classId}.`
  );
}

/* What gets saved: every class with instances, plus every zero a person asserted. The first
 * half is implied by the annotations themselves and is recomputed rather than remembered, so
 * deleting the last instance of a class does not silently leave a claim behind. */
function reviewedClassesForSave() {
  const ids = new Set(state.truth.map((t) => t.class_id));
  for (const id of state.reviewedClasses) if (!ids.has(id)) ids.add(id);
  return [...ids].sort();
}

/* Truth instances need an identity that survives their geometry being edited.
 *
 * They used to be identified by their rounded box, which works only as long as a box never
 * moves. Resizing one changes its key, so it would silently orphan itself from the detection
 * it was accepted from: re-accepting would add a duplicate and O would toggle occlusion on a
 * different record. `uid` is that identity; `detId` is the detection it came from, when it
 * came from one. Neither is persisted -- ground truth records what is on the drawing, not
 * which run of which detector proposed it -- so both are rebuilt on load. */
let truthCounter = 0;
function truthUid() {
  truthCounter += 1;
  return `t${truthCounter}`;
}

function truthById(uid) {
  return state.truth.find((t) => t.uid === uid) || null;
}

/* The truth record for a detection: by link first, falling back to geometry for instances
 * read back from disk, which carry no link. A match by geometry adopts the link, so the
 * fallback is needed at most once per instance per session.
 *
 * Geometry alone is not enough -- a page holds more than one class, and on T5 a door's box
 * and the elevation marker beside the lift overlap. Matching across classes let a door pass
 * adopt a recorded MARKER: accepting then rewrote its class to door_swing and rejecting
 * deleted it outright, silently, from a class the reviewer was not even looking at. */
function truthForDetection(detection) {
  const linked = state.truth.find((t) => t.detId === detection.id);
  if (linked) return linked;
  const key = truthKey(detection.bbox_image_px);
  const byBox = state.truth.find(
    (t) => !t.detId && t.class_id === detection.class_id && truthKey(t.bbox_image_px) === key
  );
  if (byBox) byBox.detId = detection.id;
  return byBox || null;
}

/* After a count, adopt saved instances that have no link yet: a detection OF THE SAME CLASS
 * whose centroid falls inside the truth box is the same instance. This is the harness's own
 * matching rule (eval/harness.py scores on centre distance, not IoU) and it is what keeps a
 * RESIZED box from being accepted a second time as a duplicate after a reload.
 *
 * The class test is not decoration. Reloading a sheet drops every link, so a door count then
 * saw the whole page's truth as unlinked and could bind a marker's box to a door -- one
 * keystroke from deleting it. Truth for a class nobody is counting must be untouchable. */
function relinkTruthToDetections() {
  if (!state.detections) return;
  const taken = new Set(state.truth.map((t) => t.detId).filter(Boolean));
  for (const t of state.truth) {
    if (t.detId) continue;
    const [x, y, w, h] = t.bbox_image_px;
    const hit = state.detections.find((d) => {
      if (taken.has(d.id) || d.class_id !== t.class_id) return false;
      const [dx, dy, dw, dh] = d.bbox_image_px;
      const cx = dx + dw / 2;
      const cy = dy + dh / 2;
      return cx >= x && cx <= x + w && cy >= y && cy <= y + h;
    });
    if (hit) {
      t.detId = hit.id;
      taken.add(hit.id);
    }
  }
}

/* The one place that says where the annotation stands.
 *
 * This has to be durable rather than a passing message. Ground truth survives Reset by
 * design, so after a save the sheet is still covered in recorded boxes -- and with only a
 * transient "Saved…" note, pressing Reset cleared the note, left the boxes, and looked for
 * all the world like the button had stopped working. Three states, always on screen:
 * nothing recorded, unsaved changes, everything saved. */
function markTruthDirty(dirty) {
  state.truthDirty = dirty;
  const kept = state.truth.length;
  // The occluded count is shown because it is the number the harness grades separately, and
  // a page annotated without it looks complete while measuring nothing.
  const hard = state.truth.filter((t) => t.occluded).length;
  const split = hard ? `, ${hard} occluded` : "";
  const saved = !dirty && state.truthAnnotated;

  els.truthState.classList.toggle("dirty", dirty);
  els.truthState.classList.toggle("saved", saved);

  if (!kept) {
    els.truthState.textContent = state.truthAnnotated
      ? dirty
        ? "No instances recorded — unsaved changes"
        : "✓ Saved — this sheet is recorded as having none"
      : "No instances recorded (never annotated)";
  } else {
    const body = `${kept} instance${kept === 1 ? "" : "s"} recorded${split}`;
    els.truthState.textContent = dirty
      ? `${body} — unsaved changes`
      : saved
        ? `✓ ${body} — all saved`
        : `${body} (never annotated)`;
  }
  els.truthState.title = state.truthSavedPath ? `Last saved to ${state.truthSavedPath}` : "";
  els.truthSave.disabled = !dirty;
  syncTruthLegend();
}

/* Annotations live in the browser until S writes them, and the save posts the WHOLE page --
 * so anything that replaces `state.truth` throws away an unsaved pass with no trace. Turning
 * to another sheet used to do exactly that in silence, which is how a finished pass over one
 * class can come back as nothing on disk while a later pass over another class is there.
 *
 * Asking is the whole fix: the work is a person's, minutes of it, and unrecoverable. */
function confirmDiscardTruth(what) {
  if (!state.truthDirty || !(state.truth.length || state.reviewedClasses.size)) return true;
  const n = state.truth.length;
  return window.confirm(
    `${n} annotation${n === 1 ? "" : "s"} on page ${state.page} ${n === 1 ? "is" : "are"} ` +
    `not saved.

${what} discards ${n === 1 ? "it" : "them"}. ` +
    `Cancel, then press S to save first.`
  );
}

async function loadTruth() {
  hideTruthUndo();
  state.truth = [];
  state.reviewedClasses = new Set();
  state.truthAnnotated = false;
  state.truthSavedPath = null;
  try {
    const response = await fetch(api(`/api/pages/${state.page}/truth`));
    if (response.ok) {
      const data = await response.json();
      state.truthAnnotated = data.annotated;
      state.reviewedClasses = new Set(data.reviewed_classes || []);
      state.truth = (data.instances || []).map((i) => ({
        uid: truthUid(),
        detId: null,           // relinked to a detection by geometry after the next count
        class_id: i.class_id,
        bbox_image_px: i.bbox_image_px,
        label: i.label,
        occluded: i.occluded,
      }));
    }
  } catch {
    /* an unannotated page is the normal case, not an error */
  }
  markTruthDirty(false);
  redraw();
}

/* Accepting a detection adds it to truth; rejecting takes it back out. The verdicts already
 * captured by the review bar ARE the annotation, so nothing new has to be learned. */
function truthFromVerdict(detection, verdict) {
  hideTruthUndo();
  const prior = truthForDetection(detection);
  const without = state.truth.filter((t) => t !== prior);
  if (verdict === "kept") {
    without.push({
      uid: prior ? prior.uid : truthUid(),
      detId: detection.id,
      class_id: detection.class_id,
      // A box the annotator has corrected is kept as corrected. Re-accepting must not throw
      // away the correction and snap back to the detector's box.
      bbox_image_px: prior ? prior.bbox_image_px : detection.bbox_image_px,
      label: detection.label,
      // Whether something crosses this instance is the annotator's call, made with O. It is
      // carried through a re-accept rather than reset, so toggling a verdict twice does not
      // silently discard it.
      occluded: prior ? prior.occluded : false,
    });
  } else if (prior && prior.uid === state.activeTruth) {
    state.activeTruth = null;
  }
  state.truth = without;
  markTruthDirty(true);
}

/* Recording a miss is three steps: arm, drag, confirm.
 *
 * It used to be two, in the other order -- drag, then press M -- and that order cannot work.
 * A missed symbol is one the detector has no candidate for, so the drag had nothing to snap
 * to, `/select` answered "no symbol-sized ink in that box", the selection was discarded, and
 * M was disabled at exactly the moment it was needed. Arming first also means the box is
 * taken EXACTLY as drawn: a snapped box is the union of the components the detector already
 * found, which is the wrong shape for recording something it did not find. */
function armMissed() {
  if (state.pendingMissed) return;      // already holding a box; confirm or cancel it first
  state.missedMode = true;
  // Dragging is gated on select mode, so arming has to turn it on -- otherwise the very next
  // step of the gesture the button just started is inert.
  if (!state.selectMode) setSelectMode(true);
  syncMissedUi();
  note("Missed mode: drag a box around the symbol the detector did not propose.");
  redraw();
}

function cancelMissed() {
  if (!state.missedMode && !state.pendingMissed) return;
  state.missedMode = false;
  state.pendingMissed = null;
  syncMissedUi();
  note("Missed cancelled.");
  redraw();
}

/* The drag landed while armed. Hold it for confirmation rather than committing: the box is
 * hand-drawn, and a mis-drag that silently became ground truth would be found much later. */
function capturePendingMissed(bboxImagePx) {
  state.missedMode = false;
  state.pendingMissed = { bbox_image_px: bboxImagePx };
  els.missedOccluded.checked = false;
  // Deselect: the confirm step and the selected-annotation inspector carry the same two
  // controls, and showing both at once invites editing the wrong instance.
  state.activeTruth = null;
  syncTruthEdit();
  syncMissedUi();
  redraw();
}

function commitMissed() {
  hideTruthUndo();
  if (!state.pendingMissed) return;
  const box = state.pendingMissed.bbox_image_px;
  const key = truthKey(box);
  const classId = els.missedClass.value || lastCountedClass() || "unknown";
  // Within the class. Two classes can legitimately occupy the same box -- a marker drawn
  // inside a door's swing -- and refusing the second one would make it unrecordable.
  if (state.truth.some((t) => t.class_id === classId && truthKey(t.bbox_image_px) === key)) {
    note(`That ${classId} is already recorded.`);
    cancelMissed();
    return;
  }
  const occluded = els.missedOccluded.checked;
  // Not occluded by default. A miss and an occlusion are different facts -- most misses are
  // not occluded, and an occluded instance the detector DID find has to be recordable too --
  // so occlusion is set deliberately with O rather than inferred from which key was pressed.
  const instance = {
    uid: truthUid(),
    detId: null,
    class_id: classId,
    bbox_image_px: box,
    label: null,
    occluded,
  };
  state.truth.push(instance);
  // Hand-drawn boxes are the ones most likely to need a nudge, so the new one is the box the
  // handles are on -- correcting it is the next thing you would do.
  state.activeTruth = instance.uid;
  if (!state.showTruth) setShowTruth(true);
  syncTruthEdit();
  state.pendingMissed = null;
  state.missedMode = false;
  syncMissedUi();
  markTruthDirty(true);
  note(
    `Recorded a missed ${classId}${occluded ? ", occluded" : ""}. ` +
    `${state.truth.length} instance${state.truth.length === 1 ? "" : "s"} in total.`
  );
  redraw();
}

/* The class list is the registry plus whatever the page has already counted, so a miss can be
 * recorded for a class even when nothing of it was found on this sheet. */
function syncMissedClassOptions() {
  const registered = Object.values(state.classes || {});
  const ids = registered.length
    ? registered.map((c) => ({ id: c.id, name: c.name || c.id }))
    : [{ id: "unknown", name: "unknown" }];
  const preferred = lastCountedClass();
  els.missedClass.innerHTML = "";
  for (const c of ids) {
    const option = document.createElement("option");
    option.value = c.id;
    option.textContent = c.name;
    if (c.id === preferred) option.selected = true;
    els.missedClass.appendChild(option);
  }
}

function syncMissedUi() {
  els.truthAdd.setAttribute("aria-pressed", String(state.missedMode));
  els.missedArm.hidden = !state.missedMode;
  els.missedConfirm.hidden = !state.pendingMissed;
  if (state.pendingMissed) {
    syncMissedClassOptions();
    const [, , w, h] = state.pendingMissed.bbox_image_px;
    els.missedSize.textContent =
      `${Math.round(w)} × ${Math.round(h)} px at ${state.sourceDpi} DPI`;
  }
}

/* Occlusion is the axis the eval harness reports separately, and it is the annotator's to
 * set: "a wall, a note or a dimension crosses this instance". It is deliberately NOT inferred
 * from whether the detector found it -- that would make the occluded score circular, since a
 * crossed symbol the tool found would never enter the split. */
function toggleOccluded() {
  // The box being edited wins, so O acts on whatever the handles are on; otherwise the hit
  // being reviewed; otherwise the current selection.
  let instance = state.activeTruth ? truthById(state.activeTruth) : null;
  const detection =
    state.detections && state.cursor >= 0 ? state.detections[state.cursor] : null;
  if (!instance && detection) instance = truthForDetection(detection);

  if (!instance) {
    const box = detection ? detection.bbox_image_px : state.selectedBox;
    if (!box) return;
    // Marking something occluded asserts it is really there, so it becomes truth first.
    instance = {
      uid: truthUid(),
      detId: detection ? detection.id : null,
      class_id: (detection && detection.class_id) || lastCountedClass() || "unknown",
      bbox_image_px: box,
      label: (detection && detection.label) || null,
      occluded: false,
    };
    state.truth.push(instance);
  }
  instance.occluded = !instance.occluded;
  // Show the overlay, or the only evidence of the change is a message: occlusion is drawn
  // as a heavier dash on the box itself.
  if (!state.showTruth) setShowTruth(true);
  markTruthDirty(true);
  syncTruthEdit();
  note(
    instance.occluded
      ? "Marked occluded — something crosses this instance."
      : "No longer marked occluded."
  );
  redraw();
}

function lastCountedClass() {
  return state.detections && state.detections.length ? state.detections[0].class_id : null;
}

async function saveTruth() {
  els.truthSave.disabled = true;
  els.truthSave.textContent = "Saving…";
  try {
    const response = await fetch(api(`/api/pages/${state.page}/truth`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        instances: state.truth,
        reviewed_classes: reviewedClassesForSave(),
      }),
    });
    const result = await response.json();
    if (!response.ok) {
      note(result.detail || `Could not save (${response.status}).`);
      return;
    }
    state.truthAnnotated = true;
    state.truthSavedPath = result.saved;
    hideTruthUndo();
    markTruthDirty(false);
    note(`Saved ${result.instances} instance(s) to ${result.saved}.`);
  } catch (err) {
    note(`Could not save: ${err.message}`);
  } finally {
    els.truthSave.textContent = "Save truth";
    els.truthSave.disabled = !state.truthDirty;
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
    // Escape backs out one step at a time, innermost first, so it never throws away more
    // than the gesture in progress.
    if (state.missedMode || state.pendingMissed) cancelMissed();
    else if (state.editMode) setEditMode(false);
    else if (state.selectMode || state.drag) setSelectMode(false);
    else resetAll();
    return;
  }
  if (e.key.toLowerCase() === "c") {
    e.preventDefault();
    setCandidates(!state.showCandidates);
    return;
  }
  if (e.key === "Delete" || e.key === "Backspace") {
    if (state.activeTruth) {
      e.preventDefault();
      deleteActiveTruth();
      return;
    }
  }
  if (e.key.toLowerCase() === "g") {
    e.preventDefault();
    setShowTruth(!state.showTruth);
    return;
  }
  if (e.key.toLowerCase() === "v") {
    e.preventDefault();
    setShowGrade(!state.showGrade);
    return;
  }
  if (e.key.toLowerCase() === "e") {
    e.preventDefault();
    setEditMode(!state.editMode);
    return;
  }
  const early = { m: armMissed, s: saveTruth, o: toggleOccluded };
  if (early[e.key.toLowerCase()]) {
    // Annotating does not require a count first: the whole point of "+ Missed" is to record
    // something the detector never proposed.
    e.preventDefault();
    early[e.key.toLowerCase()]();
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
    m: armMissed,
    s: saveTruth,
    o: toggleOccluded,
  };
  if (actions[key]) {
    e.preventDefault();
    actions[key]();
  }
});

// The browser's own guard, for the exit no in-page handler sees: closing the tab, reloading
// it, following a link out. The wording is the browser's; only whether to ask is ours.
window.addEventListener("beforeunload", (e) => {
  if (!state.truthDirty || !(state.truth.length || state.reviewedClasses.size)) return;
  e.preventDefault();
  e.returnValue = "";
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
  if (!confirmDiscardTruth("Opening another drawing")) return;
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
  els.editMode.addEventListener("click", () => setEditMode(!state.editMode));
  els.truthToggle.addEventListener("click", () => setShowTruth(!state.showTruth));
  els.gradeToggle.addEventListener("click", () => setShowGrade(!state.showGrade));
  els.truthClass.addEventListener("change", (e) => setActiveTruthClass(e.target.value));
  els.truthOccluded.addEventListener("change", (e) => setActiveTruthOccluded(e.target.checked));
  els.truthDelete.addEventListener("click", deleteActiveTruth);
  els.truthUndoButton.addEventListener("click", undoTruthClear);
  els.truthClear.addEventListener("click", clearAnnotations);
  els.truthDeselect.addEventListener("click", () => {
    state.activeTruth = null;
    syncTruthEdit();
    redraw();
  });
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
  els.truthAdd.addEventListener("click", () => {
    // The button is the toggle for the whole gesture: press it again to back out.
    if (state.missedMode || state.pendingMissed) cancelMissed();
    else armMissed();
  });
  els.missedCommit.addEventListener("click", commitMissed);
  els.missedCancel.addEventListener("click", cancelMissed);
  els.truthSave.addEventListener("click", saveTruth);
  await loadClasses();

  const fromUrl = Number(new URLSearchParams(location.search).get("page"));
  await load(clamp(Number.isFinite(fromUrl) && fromUrl > 0 ? fromUrl : 5, 1, meta.count));
}

main();
