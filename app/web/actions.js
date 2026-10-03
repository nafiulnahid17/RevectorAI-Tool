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
import { request, post, refresh, stage, perform } from "./api.js";
import { render } from "./views.js";
function collectRequirements() {
  return state.requirements
    .filter((r) => r.name || r.width_mm || r.height_mm)
    .map((r) => {
      if (!r.name || !r.width_mm || !r.height_mm)
        throw new Error(
          "Each size requirement needs a name, width and height.",
        );
      return {
        name: r.name,
        type:
          types.find((t) => label(t).toLowerCase() === r.name.toLowerCase()) ||
          "unknown",
        width_mm: Number(r.width_mm),
        height_mm: Number(r.height_mm),
      };
    });
}
async function upload(file) {
  if (!file) return;
  await perform(async () => {
    if (!state.health) {
      throw Object.assign(
        new Error(
          "Open the workspace at your running ReVector server to process artwork.",
        ),
        { code: "ENGINE_UNAVAILABLE" },
      );
    }
    state.operation = "Uploading source";
    const config = state.project?.settings || {
      preset: state.preset,
      vector_mode: state.mode,
      noise_reduction: state.noise ?? true,
      preserve_original_colors: state.colors ?? true,
      ocr: state.ocr ?? false,
    };
    const name =
      document.querySelector('[name="project-name"]')?.value ||
      file.name.replace(/\.[^.]+$/, "");
    state.project = await post("/projects", {
      name,
      settings: config,
      production_specifications: collectRequirements(),
    });
    const data = new FormData();
    data.append("project_id", state.project.project_id);
    data.append("file", file, file.name);
    state.project = await request("/upload", { method: "POST", body: data });
    state.selectionInitialized = false;
    state.selectedExports.clear();
    state.selected = null;
    state.step = 0;
    await refresh();
    toast("Source uploaded. Ready to analyze.");
  });
}
async function savePart() {
  const pp = part();
  const form = document.querySelector("#part-form");
  if (!pp || !form) return;
  const data = new FormData(form),
    width = data.get("part-width"),
    height = data.get("part-height");
  if (Boolean(width) !== Boolean(height))
    throw new Error("Enter both width and height, or leave both blank.");
  const payload = {
    project_id: state.project.project_id,
    name: String(data.get("part-name")).trim(),
    type: data.get("part-type"),
    physical_width_mm: width ? Number(width) : null,
    physical_height_mm: height ? Number(height) : null,
    bleed_mm: Number(data.get("part-bleed")),
    safe_zone_mm: Number(data.get("part-safe")),
    confirmed: true,
  };
  await perform(async () => {
    state.operation = "Saving part definition";
    state.project = await post("/segments/" + pp.part_id + "/update", payload);
    state.shape = null;
    toast("Part dimensions saved and boundary confirmed.");
  });
}
async function download(kind, id = null) {
  await perform(async () => {
    if (!ready())
      throw new Error("Validate and render this project before export.");
    const ids =
      kind === "part"
        ? [id]
        : kind === "selected"
          ? [...state.selectedExports]
          : state.project.parts.map((p) => p.part_id);
    const format = state.format;
    let result;
    if (kind === "master") {
      result = await stage("export", { formats: [format] });
      saveDownload(result.exports[format]);
      return;
    }
    result = await stage("export", {
      part_ids: ids,
      formats: [format, ...(kind === "selected" ? ["zip"] : [])],
    });
    if (kind === "part") {
      saveDownload(result.part_files[id][format]);
      return;
    }
    if (kind === "selected") {
      saveDownload(result.exports.zip);
      return;
    }
    result = await stage("export", { formats: [format, "zip"] });
    saveDownload(result.exports.zip);
  });
}
function saveDownload(key) {
  if (!key) throw new Error("Export did not produce a downloadable file.");
  const a = document.createElement("a");
  a.href = artifact(key);
  a.download = key.split("/").pop();
  document.body.append(a);
  a.click();
  a.remove();
  toast("Validated export is ready for download.");
}
async function vectorize(selectedOnly = false) {
  const pp = part();
  await perform(async () => {
    state.step = 3;
    state.shape = null;
    const params = selectedOnly ? { part_id: pp.part_id } : {};
    for (const s of ["reconstruct", "vectorize", "optimize"])
      await stage(s, params);
    await stage("compose");
    state.step = 4;
    await stage("validate");
    state.step = 4;
    toast(
      "Vectors rendered and validated. Review the result before exporting.",
    );
  });
}
function showReport() {
  const v = state.project.validation;
  if (!v) return;
  const div = document.querySelector("#report-content");
  div.replaceChildren();
  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(v, null, 2);
  div.append(pre);
  document.querySelector("#report-dialog").showModal();
}
async function handle(action, target) {
  if (state.busy && !["cancel", "dismiss-error"].includes(action)) return;
  switch (action) {
    case "upload":
      document.querySelector("#file-input").click();
      break;
    case "sample":
    case "sample-layout": {
      const name =
        action === "sample" ? "sample-panel.png" : "sample-layout.png";
      await upload(
        new File([await (await fetch("/assets/" + name)).blob()], name, {
          type: "image/png",
        }),
      );
      break;
    }
    case "analyze":
      await perform(async () => {
        state.step = 1;
        await stage("analyze");
      });
      break;
    case "detect":
      await perform(async () => {
        await stage("correct-geometry", { auto: state.autoGeometry || false });
        await stage("segment");
        state.step = 2;
      });
      break;
    case "vectorize":
      await vectorize();
      break;
    case "rerun":
      await vectorize(true);
      break;
    case "validate":
      await perform(async () => {
        await stage("validate");
        state.step = 4;
      });
      break;
    case "downloads":
      state.step = 5;
      render();
      break;
    case "save-part":
      await savePart();
      break;
    case "select-part":
      state.selected = target.dataset.id;
      state.shape = null;
      render();
      break;
    case "navigate":
      state.step = Number(target.dataset.step);
      state.draw = null;
      state.shape = null;
      render();
      break;
    case "back":
      state.step = Math.max(0, state.step - 1);
      state.draw = null;
      render();
      break;
    case "review-view":
      state.view = target.dataset.view;
      state.shape = null;
      render();
      break;
    case "report":
      showReport();
      break;
    case "format":
      state.format = target.dataset.format;
      render();
      break;
    case "select-all":
      state.selectedExports =
        state.selectedExports.size === state.project.parts.length
          ? new Set()
          : new Set(state.project.parts.map((p) => p.part_id));
      render();
      break;
    case "download-part":
      await download("part", target.dataset.id);
      break;
    case "download-master":
      await download("master");
      break;
    case "download-selected":
      await download("selected");
      break;
    case "download-zip":
      await download("zip");
      break;
    case "add-requirement":
      state.requirements.push({ name: "", width_mm: "", height_mm: "" });
      render();
      break;
    case "remove-requirement":
      state.requirements.splice(Number(target.dataset.index), 1);
      render();
      break;
    case "view-original":
      state.originalView = true;
      render();
      break;
    case "view-boundaries":
      state.originalView = false;
      render();
      break;
    case "draw-add":
    case "draw-update":
      state.draw = action === "draw-add" ? "add" : "update";
      state.points = [];
      state.step = 2;
      state.originalView = false;
      render();
      break;
    case "geometry":
      if (
        state.project.parts.length &&
        !confirm(
          "Correcting geometry replaces detected parts and invalidates current vectors. Continue?",
        )
      )
        return;
      state.draw = "geometry";
      state.points = [];
      state.step = 2;
      state.originalView = false;
      render();
      break;
    case "cancel-draw":
      state.draw = null;
      state.points = [];
      render();
      break;
    case "save-draw": {
      const polygon = state.points.map((p) => [...p]),
        mode = state.draw,
        id = state.selected;
      await perform(async () => {
        if (mode === "geometry") {
          await stage("correct-geometry", { corners: polygon });
          await stage("segment");
        } else if (mode === "add") {
          state.project = await post("/parts/actions", {
            project_id: state.project.project_id,
            action: "add",
            polygon,
            name: "Manual part",
          });
        } else {
          state.project = await post("/segments/" + id + "/update", {
            project_id: state.project.project_id,
            polygon,
            confirmed: false,
          });
        }
        state.draw = null;
        state.points = [];
        await refresh();
      });
      break;
    }
    case "remove-part":
      if (!confirm("Remove this part from the current project?")) return;
      await perform(async () => {
        state.project = await post("/parts/actions", {
          project_id: state.project.project_id,
          part_id: state.selected,
          action: "remove",
        });
        await refresh();
      });
      break;
    case "lock":
      await perform(async () => {
        state.project = await post("/segments/" + state.selected + "/update", {
          project_id: state.project.project_id,
          locked: !part().locked,
        });
      });
      break;
    case "preset":
      state.preset = target.dataset.value;
      if (state.project) await updateSettings({ preset: state.preset });
      else render();
      break;
    case "toggle-layer": {
      const group = document
        .querySelector("#vector-art svg")
        ?.getElementById(target.dataset.id);
      if (group) {
        group.style.display =
          group.style.display === "none"
            ? ""
            : group.getAttribute("display") === "none"
              ? "inline"
              : "none";
        target.setAttribute(
          "aria-pressed",
          group.style.display === "none" ? "false" : "true",
        );
      }
      break;
    }
    case "palette":
      if (!state.shape) {
        toast("Select a vector shape first.");
        return;
      }
      document.querySelector("#shape-color").value = target.dataset.color;
      break;
    case "apply-fill": {
      const id = state.shape,
        color = document.querySelector("#shape-color").value;
      await perform(async () => {
        state.operation = "Updating vector color";
        state.project = await post(
          "/segments/" + state.selected + "/vector-edit",
          { project_id: state.project.project_id, shape_id: id, fill: color },
        );
        state.shape = null;
        await stage("compose");
        await stage("validate");
      });
      break;
    }
    case "new-project":
      state.project = null;
      state.selected = null;
      state.step = 0;
      state.error = null;
      state.requirements = [];
      state.selectionInitialized = false;
      state.selectedExports.clear();
      try {
        localStorage.removeItem("revector.project");
      } catch {}
      render();
      break;
    case "cancel":
      state.cancel = true;
      if (state.job) await post("/jobs/" + state.job.job_id + "/cancel", {});
      toast("Cancellation requested at the next safe stage boundary.");
      break;
    case "dismiss-error":
      state.error = null;
      render();
      break;
  }
}
async function updateSettings(changes) {
  await perform(async () => {
    state.operation = "Updating processing settings";
    state.project = await request(
      "/projects/" + state.project.project_id + "/settings",
      { method: "PUT", body: JSON.stringify(changes) },
    );
  });
}

export { upload, savePart, handle, updateSettings };
