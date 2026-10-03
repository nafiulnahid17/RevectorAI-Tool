import {
  API,
  types,
  stages,
  state,
  app,
  escape,
  label,
  icon,
  badge,
  btn,
  field,
  checkbox,
  part,
  ready,
  artifact,
  picture,
  dimensions,
  toast,
} from "./model.js";
function highestStep() {
  const p = state.project;
  if (!p?.source_file) return 0;
  if (ready()) return 5;
  if (p.master_svg) return 4;
  if (p.parts.some((p) => p.vector)) return 3;
  if (p.parts.length) return 2;
  if (Object.keys(p.analysis).length) return 1;
  return 0;
}
function settingsPanel() {
  const p = state.project;
  return `<aside class="card sidebar stack"><h2>Project setup</h2><div class="project-form stack">${field("Project name", "project-name", p?.name || state.projectName || "Untitled jersey")}<label>Vector strategy<select name="vector-mode" ${state.busy ? "disabled" : ""}>${[
    ["color", "Color trace"],
    ["precision", "Precision shapes"],
    ["mono", "Single-color logo"],
    ["reconstruction", "Layer reconstruction"],
  ]
    .map(
      ([v, t]) =>
        `<option value="${v}" ${state.mode === v ? "selected" : ""}>${t}</option>`,
    )
    .join(
      "",
    )}</select></label></div><div class="divider"></div><h3>Vector detail</h3><div class="presets">${["FAST", "BALANCED", "PRECISION", "ULTRA"].map((preset) => btn(label(preset), "preset", state.preset === preset ? "selected" : "", false, `data-value="${preset}"`)).join("")}</div><p class="small muted">Balanced retains graphic detail with manageable path complexity.</p><div class="settings-detail stack"><div class="divider"></div><h3>Processing</h3>${checkbox("Reduce image noise", "noise", p?.settings.noise_reduction ?? true)}${checkbox("Preserve source colors", "colors", p?.settings.preserve_original_colors ?? true)}${checkbox("Detect text (OCR)", "ocr", p?.settings.ocr ?? false)}${checkbox("Try automatic perspective", "perspective", state.autoGeometry || false)}<p class="small muted">Perspective correction records its transform. You can mark four corners manually.</p><div class="divider"></div>${badge("OpenAI · Not connected", "warning")}<p class="small muted">Core analysis and tracing work without an API key. OpenAI processing is currently unavailable.</p></div></aside>`;
}
function partsPanel() {
  const p = state.project;
  return `<aside class="card sidebar stack"><div class="row between"><h2>Production parts</h2>${badge(p.parts.length)}</div><p class="small muted">${state.step === 5 ? "Select parts in the download panel." : "Review names, boundaries and sizes."}</p><div class="divider"></div><div class="parts-list">${p.parts.map((pp) => `<button class="part-row ${pp.part_id === state.selected ? "active" : ""}" data-action="select-part" data-id="${pp.part_id}" ${state.busy ? "disabled" : ""}>${picture(pp.corrected_crop, pp.name)}<span><strong>${escape(pp.name)}</strong><small>${escape(dimensions(pp))}</small><small class="${pp.confirmed ? "confirmed" : "unconfirmed"}">${pp.confirmed ? "Confirmed" : "Needs review"}</small></span></button>`).join("") || '<p class="muted">No parts detected yet.</p>'}</div><div class="divider"></div>${btn(icon("plus") + " Add part", "draw-add", "quiet full-width", !p.corrected_image)}${btn(icon("ruler") + " Correct geometry", "geometry", "quiet full-width", !Object.keys(p.analysis).length)}<p class="small muted">Unknown pieces need a name and category from you. No missing panels are invented.</p></aside>`;
}
function requirements() {
  return `<section class="card stack"><div class="row between"><h2>Required parts & dimensions</h2>${btn("+ Add part size", "add-requirement", "quiet", !!state.project?.source_file)}</div><p class="small muted">Optional size sheet. Match these requirements to detected parts during review.</p><div class="requirements"><div class="requirement requirements-head"><span>PART</span><span>WIDTH (mm)</span><span>HEIGHT (mm)</span><span></span></div>${state.requirements.map((r, i) => `<div class="requirement" data-index="${i}"><input name="req-name" aria-label="Required part name" placeholder="e.g. Front body" value="${escape(r.name)}"><input name="req-width" aria-label="Required width in mm" type="number" min="1" max="10000" placeholder="—" value="${escape(r.width_mm)}"><input name="req-height" aria-label="Required height in mm" type="number" min="1" max="10000" placeholder="—" value="${escape(r.height_mm)}">${btn("×", "remove-requirement", "icon-button", !!state.project?.source_file, `data-index="${i}" aria-label="Remove size requirement"`)}</div>`).join("") || '<p class="muted small">No physical sizes supplied. Exports remain uncalibrated until you enter measurements.</p>'}</div></section>`;
}
function inputMain() {
  return `<main class="main-column"><section class="card stack"><h2>Input artwork</h2><p class="muted">Upload a jersey photo or a flat artwork layout.</p><div class="dropzone" id="dropzone">${state.project?.thumbnail ? picture(state.project.thumbnail, "Uploaded artwork", "source-thumbnail") : `<div class="upload-icon">${icon("upload")}</div>`}<h2>${state.project?.source_file ? "Source uploaded" : "Drop your artwork here"}</h2><p class="small muted">JPG, PNG or WEBP · up to 50 MB</p><div class="row">${btn(state.project?.source_file ? "Replace image" : "Choose file", "upload", "primary", !state.health)}${btn("Try jersey sample", "sample", "", !state.health)}${btn("Multi-part sample", "sample-layout", "quiet", !state.health)}</div>${state.project?.source_file ? `<p class="small muted">${state.project.source_metadata.normalized_dimensions?.join(" × ")} px · original preserved</p>` : ""}</div></section>${requirements()}</main>`;
}
function analysisMain() {
  const q = state.project?.analysis;
  return `<main class="main-column"><section class="card stack"><div class="row between"><h2>Source analysis</h2>${badge(q?.quality ? "Measured diagnostics" : "Ready to analyze", "purple")}</div><div class="canvas-viewport">${picture(state.project.working_image, "Normalized original artwork")}</div>${
    q?.quality
      ? `<div class="analysis-grid">${[
          ["Resolution", `${q.resolution.width} × ${q.resolution.height}`],
          ["Sharpness", q.quality.sharpness_laplacian_variance.toFixed(1)],
          ["Brightness", (q.quality.brightness * 100).toFixed(1) + "%"],
          ["Noise estimate", q.quality.noise_score.toFixed(3)],
        ]
          .map(
            ([k, v]) =>
              `<div class="metric"><strong>${escape(v)}</strong><span>${k}</span></div>`,
          )
          .join(
            "",
          )}</div><div class="note ${q.quality.lighting_issue || q.quality.perspective_issue ? "warning" : ""}">${q.quality.perspective_issue ? "Possible perspective distortion. " : ""}${q.quality.lighting_issue ? "Possible uneven lighting. " : ""}These are CV heuristics; white and black graphic areas can trigger lighting flags.</div>`
      : ""
  }</section></main>`;
}
function correctedSize() {
  return (
    state.project.geometry.output_dimensions ||
    state.project.source_metadata.normalized_dimensions || [1, 1]
  );
}
function definitionMain() {
  const p = state.project,
    [w, h] = correctedSize();
  const drawing = !!state.draw;
  return `<main class="main-column"><section class="card stack"><div class="toolbar"><div class="row"><h2>Detected layout</h2>${badge(`${p.parts.length} parts`, "purple")}</div><div class="tabs">${btn("Original", "view-original", "tab", drawing)}${btn("Boundaries", "view-boundaries", "tab selected", drawing)}</div></div><div class="canvas-viewport"><div class="artboard ${drawing ? "draw-mode" : ""}" style="width:${Math.min(w, 760)}px"><img src="${artifact(state.originalView ? p.working_image : p.corrected_image)}" alt="Corrected source and detected part boundaries"><svg class="overlay" id="boundary-canvas" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${!drawing && !state.originalView ? p.parts.map((pp) => (pp.polygon?.length ? `<polygon class="boundary ${pp.part_id === state.selected ? "selected" : ""} ${pp.confirmed ? "confirmed" : ""}" data-id="${pp.part_id}" points="${pp.polygon.map((q) => q.join(",")).join(" ")}"/>` : `<rect class="boundary ${pp.part_id === state.selected ? "selected" : ""} ${pp.confirmed ? "confirmed" : ""}" data-id="${pp.part_id}" x="${pp.bbox[0]}" y="${pp.bbox[1]}" width="${pp.bbox[2]}" height="${pp.bbox[3]}"/>`)).join("") : ""}${drawing ? drawingMarkup() : ""}</svg></div></div><div class="toolbar"><div class="row">${btn(icon("pen") + (drawing ? "Cancel drawing" : "Redraw selected boundary"), drawing ? "cancel-draw" : "draw-update", "", !part() || part()?.locked)}${drawing ? btn("Save boundary", "save-draw", "primary", state.points.length < (state.draw === "geometry" ? 4 : 3)) : btn("Remove part", "remove-part", "quiet danger", !part() || part()?.locked)}</div><span class="small muted">${drawing ? "Click points on the image · " + state.points.length + " points" : "Corrected source coordinates"}</span></div>${drawing ? `<p class="note">${state.draw === "geometry" ? "Mark four corners clockwise, starting at the top-left. This replaces existing segmentation." : "Click around the part boundary, then save. At least three points are required."}</p>` : ""}</section><section class="card stack"><div class="row between"><h3>${p.parts.filter((pp) => !pp.confirmed).length} parts need confirmation</h3>${badge("Human review required", "warning")}</div><p class="small muted">Check the outlines, assign each category and enter your production dimensions before tracing.</p></section></main>`;
}
function drawingMarkup() {
  return `<polyline class="drawing-line" points="${state.points.map((p) => p.join(",")).join(" ")}"/>${state.points.map(([x, y]) => `<circle class="draw-point" cx="${x}" cy="${y}" r="${Math.max(...correctedSize()) * 0.006}"/>`).join("")}`;
}
function reviewMain() {
  const p = state.project,
    pp = part(),
    paths = state.view === "paths";
  const vectorKey =
    pp && pp.cache.optimize && p.master_svg
      ? `projects/${p.project_id}/vectors/${pp.part_id}.svg`
      : null;
  const fallback = p.previews.vector_render || pp?.vector;
  const original =
    pp?.clean_reference || pp?.corrected_crop || p.previews.reference;
  return `<main class="main-column"><section class="card stack"><div class="toolbar"><div class="row"><h2>${escape(pp?.name || "Vector review")}</h2>${badge(ready() ? "True Vector validated" : "Validation pending", ready() ? "success" : "warning")}</div><div class="tabs">${["compare", "vector", "paths"].map((v) => btn(label(v), "review-view", "tab " + (state.view === v ? "selected" : ""), false, `data-view="${v}"`)).join("")}</div></div><div class="${state.view === "compare" ? "compare-grid" : "wide-preview"}">${state.view === "compare" ? `<div class="preview-pane"><div class="row between"><span>Clean raster reference</span>${badge("Raster")}</div><div class="preview-art">${picture(original, "Clean raster reference")}</div></div>` : ""}<div class="preview-pane"><div class="row between"><span>${paths ? "Vector paths" : "Reconstructed artwork"}</span>${badge(paths ? "Path view" : "Editable objects", "purple")}</div><div class="preview-art" id="vector-art">${paths ? picture(p.previews.vector_view, "Diagnostic vector paths") : vectorKey ? picture(vectorKey, "Composed editable vector part") : picture(fallback, "Vector preview")}</div></div></div><div class="toolbar"><p class="comparison-caption">${paths ? "Diagnostic view shows the master paths and cut geometry." : "Click a vector shape to inspect or change its solid fill."}</p>${btn("Rerun selected part", "rerun", "quiet", !pp)}</div></section><section class="card stack"><div class="row between"><h3>Artwork colors</h3><span class="small muted">${pp?.palette.length || 0} palette colors</span></div><div class="swatches">${
    (pp?.palette || [])
      .slice(0, 24)
      .map(
        (c) =>
          `<button class="swatch" style="background:${/^#[0-9a-f]{6}$/i.test(c.hex) ? c.hex : "#ddd"}" data-action="palette" data-color="${escape(c.hex)}" title="${escape(c.hex)}" aria-label="Use palette color ${escape(c.hex)}"></button>`,
      )
      .join("") ||
    '<span class="muted small">Colors appear after reconstruction.</span>'
  }</div>${p.validation?.visual_comparison ? `<p class="small muted">Visual comparison SSIM: ${Number(p.validation.visual_comparison.ssim ?? 0).toFixed(3)} · advisory, not a pixel-perfect guarantee.</p>` : ""}</section></main>`;
}
function downloadsMain() {
  const p = state.project;
  return `<main class="main-column"><section class="card stack"><div class="row between"><h2>Choose parts to download</h2>${btn(state.selectedExports.size === p.parts.length ? "Deselect all" : "Select all", "select-all", "quiet")}</div><p class="muted small">Download a single part, selected parts, or the complete project.</p><div class="part-tiles">${p.parts.map((pp) => `<div class="part-tile ${state.selectedExports.has(pp.part_id) ? "checked" : ""}"><label><input type="checkbox" name="export-part" value="${pp.part_id}" ${state.selectedExports.has(pp.part_id) ? "checked" : ""} ${state.busy ? "disabled" : ""}>${escape(pp.name)}</label>${picture(`projects/${p.project_id}/vectors/${pp.part_id}.svg`, pp.name)}<small class="muted">${escape(dimensions(pp))}</small>${badge("Validated", "success")}</div>`).join("")}</div></section><section class="card stack"><h2>Download format</h2><div class="formats">${[
    ["svg", "Editable master"],
    ["eps", "Vector production"],
    ["pdf", "Editable vector PDF"],
    ["ai", "Illustrator required"],
  ]
    .map(([f, s]) =>
      btn(
        `<strong>${f.toUpperCase()}</strong><small>${s}</small>`,
        "format",
        "format " + (state.format === f ? "selected" : ""),
        f === "ai" ||
          (["eps", "pdf"].includes(f) && !state.health?.dependencies.inkscape),
        `data-format="${f}" ${f === "ai" ? 'title="Native AI requires Adobe Illustrator automation"' : ""}`,
      ),
    )
    .join(
      "",
    )}</div><p class="small muted">SVG, EPS and vector PDF open in Illustrator. Native .AI export requires Illustrator automation.</p></section><section class="card stack"><h2>Individual downloads</h2><table class="file-table"><tbody>${p.parts.map((pp) => `<tr><td>${picture(pp.corrected_crop, pp.name)}</td><td>${escape(pp.name)}</td><td class="muted dimension-cell">${escape(dimensions(pp))}</td><td>${btn(icon("download") + " " + state.format.toUpperCase(), "download-part", "", !ready(), `data-id="${pp.part_id}"`)}</td></tr>`).join("")}</tbody></table></section></main>`;
}
function inputInspector() {
  return `<aside class="card inspector stack"><h2>Before you analyze</h2><p class="small muted">A clear source makes cleaner paths.</p><ul class="tips"><li>Keep every panel visible</li><li>Use a straight, well-lit photo</li><li>Include logos at full resolution</li></ul><div class="divider"></div><h3>What happens next</h3>${["Analyze source quality", "Detect visible components", "Review boundaries & dimensions", "Create and validate real vectors"].map((s, i) => `<div class="row"><span class="badge purple">0${i + 1}</span><span class="small">${s}</span></div>`).join("")}<div class="divider"></div><h3>Physical dimensions</h3><p class="small muted">Measurements come from your size sheet or manual input. A photograph does not establish garment size.</p>${badge("Dimensions require review", "warning")}<div class="divider"></div><a class="small" href="/docs" target="_blank" rel="noopener">Engine API documentation ↗</a></aside>`;
}
function partInspector() {
  const pp = part(),
    p = state.project;
  if (!pp) return inputInspector();
  return `<aside class="card inspector stack"><div class="row between"><h2>Selected part</h2>${badge(pp.confirmed ? "Confirmed" : "Needs review", pp.confirmed ? "success" : "warning")}</div><form id="part-form" class="stack">${field("Part name", "part-name", pp.name, "text", 'maxlength="120" required')}<label>Category<select name="part-type" ${state.busy || pp.locked ? "disabled" : ""}>${types.map((t) => `<option value="${t}" ${pp.type === t ? "selected" : ""}>${label(t)}</option>`).join("")}</select></label>${p.production_specifications?.length ? `<label>Apply size requirement<select name="size-requirement"><option value="">Choose a part from your size sheet</option>${p.production_specifications.map((r, i) => `<option value="${i}">${escape(r.name)} · ${r.width_mm} × ${r.height_mm} mm</option>`).join("")}</select></label>` : ""}<div class="divider"></div><h3>Production dimensions</h3><div class="two-fields">${field("Width (mm)", "part-width", pp.physical_width_mm || "", "number", 'min="0.1" max="10000" step="0.1"')}${field("Height (mm)", "part-height", pp.physical_height_mm || "", "number", 'min="0.1" max="10000" step="0.1"')}</div>${checkbox("Keep artwork aspect ratio", "aspect-lock", true)}<div class="two-fields">${field("Bleed (mm)", "part-bleed", pp.bleed_mm, "number", 'min="0" max="100" step="0.1"')}${field("Safe zone (mm)", "part-safe", pp.safe_zone_mm, "number", 'min="0" max="100" step="0.1"')}</div><p class="small muted">Individual exports use these dimensions. Leave both blank for pixel units.</p>${pp.locked ? badge("Part locked", "warning") : btn("Save & confirm part", "save-part", "primary full-width")}<div class="row">${btn(pp.locked ? "Unlock part" : "Lock part", "lock", "quiet full-width")}</div></form><div class="divider"></div>${btn(icon("pen") + " Redraw boundary", "draw-update", "", pp.locked)}<p class="small muted">Boundary edits rerun only this part. Review sizing before production.</p></aside>`;
}
function validationInspector() {
  const v = state.project.validation,
    pp = part();
  return `<aside class="card inspector stack"><h2>Layers / ${escape(pp?.name || "Artwork")}</h2><div id="layer-list" class="stack" style="gap:3px"><p class="small muted">${state.layers.length ? "" : "Layers load from the real composed SVG."}</p>${state.layers.map((l) => `<div class="layer-row">${btn(icon("eye"), "toggle-layer", "icon-button", false, `data-id="${escape(l.id)}" aria-label="Toggle ${escape(l.name)} visibility"`)}<span>${escape(l.name)}</span><span class="layer-count">${l.count}</span></div>`).join("")}</div><div class="divider"></div><h3>Selected shape</h3><p class="small muted" id="shape-label">${escape(state.shape || "Click a shape in the vector preview.")}</p><input type="color" class="color-input" id="shape-color" aria-label="Selected shape fill" value="#6d3dee" ${!state.shape || state.busy ? "disabled" : ""}>${btn("Apply color & revalidate", "apply-fill", "full-width", !state.shape)}<div class="divider"></div><div class="row between"><h3>Validation</h3>${badge(v ? (ready() ? "PASS" : "FAILED") : "Pending", v ? (ready() ? "success" : "error") : "warning")}</div><div class="report-metrics">${[
    ["Embedded rasters", v?.embedded_rasters],
    ["Vector paths", v?.path_count],
    ["Groups", v?.group_count],
    ["Anchor points", v?.total_anchor_count],
  ]
    .map(
      ([k, val]) =>
        `<div><span>${k}</span><strong>${val === undefined ? "—" : Number(val).toLocaleString()}</strong></div>`,
    )
    .join(
      "",
    )}</div><div class="divider"></div><h3>Illustrator compatibility</h3>${badge(v?.illustrator_compatibility?.replaceAll("_", " ") || "Not validated", v?.illustrator_compatibility === "PASS" ? "success" : v?.illustrator_compatibility === "FAIL" ? "error" : "warning")}<p class="small muted">Standard SVG checks; actual Illustrator appearance and print sizes need review.</p>${btn("View validation report", "report", "quiet full-width", !v)}${v?.warnings?.length ? `<p class="note warning validation-warning">${v.warnings.map(escape).join("<br>")}</p>` : ""}</aside>`;
}
function exportInspector() {
  const p = state.project;
  const n = state.selectedExports.size;
  return `<aside class="card inspector stack"><h2>Production pack</h2>${badge(ready() ? "True Vector validated" : "Validation required", ready() ? "success" : "warning")}<strong>${escape(p.name)}</strong><p class="small muted">${n} selected parts · ${state.format.toUpperCase()}</p><div class="divider"></div><h3>Included in ZIP</h3><p class="small muted">• Individual vector files<br>• Available master exports<br>• Reference & vector previews<br>• Palette and project metadata<br>• Validation report</p><div class="divider"></div><h2 style="color:var(--brand)">${state.format.toUpperCase()}</h2><p class="small muted">Real paths, separate groups and editable objects.</p>${btn(icon("download") + " Selected parts ZIP", "download-selected", "", !ready() || !n)}${btn(icon("download") + " Download full ZIP", "download-zip", "primary", !ready())}${btn("Download master " + state.format.toUpperCase(), "download-master", "quiet", !ready())}<div class="divider"></div><h3>Illustrator handoff</h3><p class="small muted">Individual part files carry their supplied sizes. The master preserves the source layout and its project calibration.</p>${badge(p.parts.every((p) => p.physical_width_mm) ? "Part sizes calibrated" : "Some sizes uncalibrated", p.parts.every((p) => p.physical_width_mm) ? "success" : "warning")}</aside>`;
}
function footer() {
  const max = highestStep(),
    p = state.project;
  let title = "Your original image is preserved.",
    cta = "Analyze input",
    action = "analyze",
    disabled = !p?.source_file;
  if (state.step === 1) {
    title = "Review quality, then correct geometry and detect parts.";
    cta = "Detect parts";
    action = "detect";
    disabled = !p?.analysis?.quality;
  }
  if (state.step === 2) {
    title = "Confirm each boundary and provide physical sizes if required.";
    cta = "Vectorize confirmed parts";
    action = "vectorize";
    disabled =
      !p?.parts.length || p.parts.some((p) => !p.confirmed) || !!state.draw;
  }
  if (state.step === 3 || state.step === 4) {
    title =
      "Validate XML integrity and render the current vector before export.";
    cta = ready() ? "Continue to download" : "Validate vectors";
    action = ready() ? "downloads" : "validate";
    disabled = !p?.master_svg;
  }
  if (state.step === 5) {
    title = "Only validated vectors can be exported.";
    cta = "Download full ZIP";
    action = "download-zip";
    disabled = !ready();
  }
  return `<footer class="bottom-bar"><p class="small muted">${title}</p><div class="actions">${state.busy ? `<span class="small muted">${escape(state.operation)}…</span><button data-action="cancel">Cancel</button>` : `${state.step > 0 ? btn("Back", "back", "") : ""}${btn(cta, action, "primary", disabled)}`}</div></footer>`;
}
function connectionNotice() {
  return `<section class="connection-notice" role="status"><div><strong>Workspace preview · Engine not connected</strong><p>This preview displays the interface. Upload, vectorization and exports require the running ReVector server.</p><p>In your project folder, run <code>docker compose up --build</code>, then open the workspace at your server address.</p></div><div class="connection-actions"><a class="engine-link" href="http://localhost:8000/" target="_blank" rel="noopener">Open local workspace ↗</a>${btn("Retry connection", "retry-connection", "")}</div></section>`;
}
function render() {
  const p = state.project,
    max = highestStep();
  app.innerHTML = `<header class="topbar"><div class="brand"><span class="brand-mark">${icon("pen")}</span><h1>ReVector</h1></div><div class="breadcrumb"><span class="muted small">Projects</span>${icon("chevron")}<span class="project-name">${escape(p?.name || "New production project")}</span></div><div class="right">${badge(state.health ? "Engine connected" : state.connecting ? "Connecting to engine" : "Engine unavailable", state.health ? "success" : "warning")}${btn("New project", "new-project", "quiet")}<a class="icon-button muted" href="/docs" target="_blank" aria-label="API documentation">${icon("settings")}</a><span class="avatar">R</span></div></header><nav class="stepper" aria-label="Processing workflow">${stages.map((s, i) => `<button class="step ${state.step === i ? "active" : i < state.step ? "complete" : ""}" data-action="navigate" data-step="${i}" ${state.busy || i > max ? "disabled" : ""}><span class="step-number">${i < state.step ? icon("check") : i + 1}</span><span><strong>${s}</strong><small>${state.step === i ? "Current step" : i < state.step ? "Complete" : "Upcoming"}</small></span></button>`).join("")}</nav>${!state.health && !state.connecting ? connectionNotice() : ""}${state.busy ? `<div class="busy-banner" role="status"><span class="spinner"></span>${escape(state.operation || "Working")}… ${state.job ? '<span class="small">Background job · ' + escape(state.job.status) + "</span>" : ""}</div>` : ""}${state.error ? `<div class="status-error" role="alert"><strong>${escape(state.error.code)}</strong><span>${escape(state.error.message)}</span><button data-action="dismiss-error" class="quiet">Dismiss</button></div>` : ""}<div class="workspace">${state.step < 2 ? settingsPanel() : partsPanel()}${state.step === 0 ? inputMain() : state.step === 1 ? analysisMain() : state.step === 2 ? definitionMain() : state.step === 5 ? downloadsMain() : reviewMain()}${state.step < 2 ? inputInspector() : state.step === 2 ? partInspector() : state.step === 5 ? exportInspector() : validationInspector()}</div>${footer()}`;
  bindCanvas();
  if ([3, 4].includes(state.step) && !state.busy && state.view !== "paths")
    loadVector();
}
async function loadVector() {
  const p = state.project,
    pp = part(),
    container = document.querySelector("#vector-art");
  if (!pp || !container || !p.master_svg) return;
  const id = pp.part_id,
    version = p.updated_at;
  try {
    const response = await fetch(
      artifact(`projects/${p.project_id}/vectors/${id}.svg`),
    );
    if (!response.ok) return;
    const raw = await response.text();
    if (
      state.selected !== id ||
      state.project.updated_at !== version ||
      !container.isConnected
    )
      return;
    const doc = new DOMParser().parseFromString(raw, "image/svg+xml");
    if (doc.querySelector("parsererror,script,foreignObject,image")) return;
    const svg = doc.documentElement;
    for (const el of svg.querySelectorAll("*"))
      for (const a of [...el.attributes])
        if (
          a.name.startsWith("on") ||
          /(?:https?:|javascript:|data:)/i.test(a.value)
        )
          return;
    container.replaceChildren(document.importNode(svg, true));
    state.editSvg = raw;
    const live = container.firstElementChild;
    state.layers = [...live.querySelectorAll("g[id]")]
      .filter((g) =>
        /_(CUT_PATH|BLEED_PATH|SAFE_ZONE|BASE_COLOR|GRADIENT|PATTERN|SIDE_GRAPHICS|LOGO|TEXT|DETAILS)$/.test(
          g.id,
        ),
      )
      .map((g) => ({
        id: g.id,
        name: g.id.replace(id + "_", ""),
        count: g.querySelectorAll(
          "path,rect,circle,ellipse,polygon,polyline,line,text",
        ).length,
      }));
    const list = document.querySelector("#layer-list");
    if (list)
      list.innerHTML = state.layers
        .map(
          (l) =>
            `<div class="layer-row"><button class="icon-button quiet" data-action="toggle-layer" data-id="${escape(l.id)}" aria-label="Toggle ${escape(l.name)} visibility">${icon("eye")}</button><span>${escape(l.name)}</span><span class="layer-count">${l.count}</span></div>`,
        )
        .join("");
    for (const shape of live.querySelectorAll(
      "path[id],rect[id],circle[id],ellipse[id],polygon[id]",
    )) {
      if (shape.closest("defs") || shape.closest('g[display="none"]')) continue;
      shape.classList.add("editable-shape");
      shape.addEventListener("click", () => {
        live
          .querySelectorAll(".selected-shape")
          .forEach((s) => s.classList.remove("selected-shape"));
        shape.classList.add("selected-shape");
        state.shape = shape.id;
        document.querySelector("#shape-label").textContent = shape.id;
        const input = document.querySelector("#shape-color");
        input.disabled = false;
        input.value = /^#[0-9a-f]{6}$/i.test(shape.getAttribute("fill") || "")
          ? shape.getAttribute("fill")
          : "#6d3dee";
        document.querySelector('[data-action="apply-fill"]').disabled = false;
      });
    }
  } catch (error) {
    toast("Vector preview could not load: " + error.message);
  }
}
function bindCanvas() {
  const canvas = document.querySelector("#boundary-canvas");
  if (!canvas || state.busy) return;
  canvas.addEventListener("click", (e) => {
    if (!state.draw) {
      const id = e.target.dataset.id;
      if (id) {
        state.selected = id;
        state.shape = null;
        render();
      }
      return;
    }
    if (state.draw === "geometry" && state.points.length === 4) return;
    const box = canvas.getBoundingClientRect(),
      [w, h] = correctedSize();
    state.points.push([
      Math.round(
        Math.min(w - 1, Math.max(0, ((e.clientX - box.left) / box.width) * w)),
      ),
      Math.round(
        Math.min(h - 1, Math.max(0, ((e.clientY - box.top) / box.height) * h)),
      ),
    ]);
    canvas.innerHTML = drawingMarkup();
    const save = document.querySelector('[data-action="save-draw"]');
    save.disabled = state.points.length < (state.draw === "geometry" ? 4 : 3);
    const status = canvas
      .closest(".card")
      .querySelector(".toolbar:last-of-type .muted");
    if (status) status.textContent = state.points.length + " points";
  });
}

export { render, highestStep };
