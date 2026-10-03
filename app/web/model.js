const API = "/api/revector";
const types = [
  "unknown",
  "front_body",
  "back_body",
  "left_sleeve",
  "right_sleeve",
  "front_collar",
  "back_collar",
  "left_shoulder",
  "right_shoulder",
  "left_cuff",
  "right_cuff",
  "trim",
  "other_part",
];
const stages = [
  "Input",
  "Analyze",
  "Define parts",
  "Vectorize",
  "Validate",
  "Download",
];
const state = {
  project: null,
  selected: null,
  step: 0,
  preset: "BALANCED",
  mode: "color",
  view: "compare",
  busy: false,
  job: null,
  operation: "",
  error: null,
  health: null,
  connecting: true,
  connections: { server: "pending", engine: "pending", tool: "pending" },
  connectionMessage: "",
  selectedExports: new Set(),
  format: "svg",
  draw: null,
  points: [],
  cancel: false,
  requirements: [],
  layers: [],
  shape: null,
  editSvg: null,
};
const app = document.querySelector("#app");
const escape = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const label = (s) =>
  s.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
const iconPaths = {
  pen: "m4 16-1 5 5-1L20 8l-4-4L4 16Zm11-11 4 4",
  upload: "M12 16V4m-4 4 4-4 4 4M4 16v4h16v-4",
  download: "M12 4v12m-4-4 4 4 4-4M4 18v3h16v-3",
  check: "m5 12 4 4 10-10",
  chevron: "m9 5 7 7-7 7",
  eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Zm10-3a3 3 0 1 0 0 6 3 3 0 0 0 0-6",
  layers: "M12 3 2 8l10 5 10-5-10-5ZM2 12l10 5 10-5M2 16l10 5 10-5",
  plus: "M12 4v16M4 12h16",
  ruler: "M3 5h18v14H3V5Zm4 0v6m5-6v4m5-4v6",
  settings: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8M3 12h2m14 0h2M12 3v2m0 14v2",
};
const icon = (name) =>
  `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${iconPaths[name] || iconPaths.layers}"/></svg>`;
const badge = (text, tone = "") =>
  `<span class="badge ${tone}">${escape(text)}</span>`;
const btn = (text, action, tone = "", disabled = false, extra = "") =>
  `<button class="${tone}" data-action="${action}" ${state.busy || disabled ? "disabled" : ""} ${extra}>${text}</button>`;
const field = (title, name, value, type = "text", extra = "") =>
  `<label>${escape(title)}<input name="${name}" type="${type}" value="${escape(value)}" ${extra} ${state.busy ? "disabled" : ""}></label>`;
const checkbox = (title, name, checked, disabled = false) =>
  `<label class="checkbox"><input type="checkbox" name="${name}" ${checked ? "checked" : ""} ${state.busy || disabled ? "disabled" : ""}>${escape(title)}</label>`;
const part = () =>
  state.project?.parts.find((p) => p.part_id === state.selected);
const ready = () => state.project?.true_vector_ready === true;
function artifact(key) {
  if (!key || !state.project) return "";
  const prefix = `projects/${state.project.project_id}/`;
  if (!key.startsWith(prefix)) return "";
  return `${API}/projects/${encodeURIComponent(state.project.project_id)}/artifacts/${key.slice(prefix.length).split("/").map(encodeURIComponent).join("/")}?v=${encodeURIComponent(state.project.updated_at)}`;
}
const picture = (key, alt, cls = "") =>
  key ? `<img class="${cls}" src="${artifact(key)}" alt="${escape(alt)}">` : "";
const dimensions = (p) =>
  p.physical_width_mm
    ? `${p.physical_width_mm} × ${p.physical_height_mm} mm`
    : `${p.bbox[2]} × ${p.bbox[3]} px · uncalibrated`;
function toast(message) {
  const t = document.querySelector("#toast");
  t.textContent = message;
  t.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("show"), 4500);
}

export {
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
};
