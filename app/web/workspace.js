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
import { request, refresh } from "./api.js";
import { render, highestStep } from "./views.js";
import { upload, savePart, handle, updateSettings } from "./actions.js";
app.addEventListener("click", (event) => {
  const target = event.target.closest("[data-action]");
  if (target) {
    event.preventDefault();
    if (target.dataset.action === "retry-connection") {
      boot();
      return;
    }
    handle(target.dataset.action, target).catch((error) => {
      state.error = {
        code: error.code || "INPUT_ERROR",
        message: error.message,
      };
      render();
    });
  }
});
app.addEventListener("submit", (event) => {
  event.preventDefault();
  if (event.target.id === "part-form")
    savePart().catch((error) => toast(error.message));
});
app.addEventListener("input", (event) => {
  const el = event.target;
  if (el.name?.startsWith("req-")) {
    const r = state.requirements[el.closest("[data-index]").dataset.index];
    r[
      {
        "req-name": "name",
        "req-width": "width_mm",
        "req-height": "height_mm",
      }[el.name]
    ] = el.value;
  }
  if (el.name === "project-name") state.projectName = el.value;
  if (
    el.name === "part-width" &&
    document.querySelector('[name="aspect-lock"]')?.checked &&
    part()
  ) {
    const h = document.querySelector('[name="part-height"]');
    h.value = el.value
      ? ((Number(el.value) * part().bbox[3]) / part().bbox[2]).toFixed(1)
      : "";
  }
});
app.addEventListener("change", (event) => {
  const el = event.target;
  if (el.name === "export-part") {
    el.checked
      ? state.selectedExports.add(el.value)
      : state.selectedExports.delete(el.value);
    render();
  }
  if (el.name === "size-requirement" && el.value !== "") {
    const r = state.project.production_specifications[Number(el.value)];
    document.querySelector('[name="part-name"]').value = r.name;
    document.querySelector('[name="part-type"]').value = r.type;
    document.querySelector('[name="part-width"]').value = r.width_mm;
    document.querySelector('[name="part-height"]').value = r.height_mm;
    document.querySelector('[name="aspect-lock"]').checked = false;
  }
  if (el.name === "perspective") state.autoGeometry = el.checked;
  if (["noise", "colors", "ocr", "vector-mode"].includes(el.name)) {
    const key = {
      noise: "noise_reduction",
      colors: "preserve_original_colors",
      ocr: "ocr",
      "vector-mode": "vector_mode",
    }[el.name];
    const value = el.name === "vector-mode" ? el.value : el.checked;
    if (state.project) updateSettings({ [key]: value });
    else {
      if (el.name === "vector-mode") state.mode = value;
      else state[el.name] = value;
    }
  }
});
app.addEventListener("dragover", (event) => {
  const zone = event.target.closest("#dropzone");
  if (zone) {
    event.preventDefault();
    zone.classList.add("drag-over");
  }
});
app.addEventListener("dragleave", (event) =>
  event.target.closest("#dropzone")?.classList.remove("drag-over"),
);
app.addEventListener("drop", (event) => {
  if (event.target.closest("#dropzone")) {
    event.preventDefault();
    if (!state.busy) upload(event.dataTransfer.files[0]);
  }
});
document.querySelector("#file-input").addEventListener("change", (event) => {
  upload(event.target.files[0]);
  event.target.value = "";
});
document
  .querySelector("#close-report")
  .addEventListener("click", () =>
    document.querySelector("#report-dialog").close(),
  );
async function boot() {
  if (boot.running) return;
  boot.running = true;
  state.connecting = true;
  state.health = null;
  state.connections = { server: "pending", engine: "pending", tool: "pending" };
  state.connectionMessage = "";
  render();
  try {
    if (location.protocol === "file:" || location.origin === "null") {
      throw new Error("The server could not be reached from this preview.");
    }
    const server = await request("/health");
    if (server?.engine !== "ReVector" || server?.status !== "ok") {
      throw new Error("ReVector server did not respond.");
    }
    state.connections.server = "connected";
    render();
    let report;
    try {
      report = await request("/health/ready");
    } catch (error) {
      if (!error.data?.segments) throw error;
      report = error.data;
    }
    if (report?.engine !== "ReVector" || !report.segments) {
      throw new Error("The engine readiness check did not respond.");
    }
    for (const key of ["engine", "tool"]) {
      state.connections[key] =
        report.segments[key]?.status === "connected" ? "connected" : "failed";
    }
    if (
      Object.values(state.connections).some((status) => status !== "connected")
    ) {
      state.connectionMessage =
        state.connections.engine === "failed"
          ? "Engine checks failed."
          : "Tool checks failed.";
      return;
    }
    state.health = report;
    let saved;
    try {
      saved = localStorage.getItem("revector.project");
    } catch {}
    if (saved && !state.project) {
      try {
        state.project = { project_id: saved };
        await refresh();
        state.requirements = state.project.production_specifications || [];
        state.step = highestStep();
      } catch {
        state.project = null;
        try {
          localStorage.removeItem("revector.project");
        } catch {}
      }
    }
  } catch (error) {
    const failed =
      state.connections.server !== "connected" ? "server" : "engine";
    state.connections[failed] = "failed";
    if (failed === "server") {
      state.connections.engine = "waiting";
      state.connections.tool = "waiting";
    } else {
      state.connections.tool = "waiting";
    }
    state.connectionMessage =
      failed === "server"
        ? "Server connection failed."
        : "Engine connection failed.";
  } finally {
    state.connecting = false;
    boot.running = false;
    render();
  }
}
if (globalThis.REVECTOR_PRERENDER) render();
else boot();
