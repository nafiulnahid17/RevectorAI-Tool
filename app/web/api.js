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
import { render } from "./views.js";
async function request(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(
    () => controller.abort(),
    path.startsWith("/health") ? 5000 : 30000,
  );
  let response;
  let data;
  try {
    response = await fetch(
      path.startsWith("/health") || path.startsWith(API + "/")
        ? path
        : API + path,
      {
        ...options,
        signal: options.signal || controller.signal,
        headers: {
          ...(!options.body || options.body instanceof FormData
            ? {}
            : { "Content-Type": "application/json" }),
          ...options.headers,
        },
      },
    );
    data = await response.json().catch((error) => {
      if (controller.signal.aborted) throw error;
      return null;
    });
  } finally {
    clearTimeout(timeout);
  }
  if (!response.ok) {
    const error = new Error(
      data?.error?.message ||
        data?.detail?.[0]?.msg ||
        data?.detail ||
        `Request failed (${response.status})`,
    );
    error.code = data?.error?.code || `HTTP_${response.status}`;
    error.data = data;
    throw error;
  }
  return data;
}
const post = (path, body) =>
  request(path, { method: "POST", body: JSON.stringify(body) });
async function refresh() {
  if (!state.project) return;
  state.project = await request(`/projects/${state.project.project_id}`);
  state.preset = state.project.settings.preset;
  state.mode = state.project.settings.vector_mode;
  const ids = new Set(state.project.parts.map((p) => p.part_id));
  if (!ids.has(state.selected))
    state.selected = state.project.parts[0]?.part_id || null;
  state.selectedExports = new Set(
    [...state.selectedExports].filter((id) => ids.has(id)),
  );
  for (const id of ids)
    if (!state.selectionInitialized) state.selectedExports.add(id);
  if (ids.size) state.selectionInitialized = true;
  try {
    localStorage.setItem("revector.project", state.project.project_id);
  } catch {
    // Sandboxed previews may block storage; the current project still works.
  }
}
async function stage(name, params = {}) {
  if (state.cancel)
    throw Object.assign(new Error("Processing cancelled."), {
      code: "JOB_CANCELLED",
    });
  state.operation = label(name.replaceAll("-", "_"));
  state.job = await post("/" + name, {
    project_id: state.project.project_id,
    ...params,
  });
  render();
  while (["queued", "processing", "cancelling"].includes(state.job.status)) {
    await new Promise((r) => setTimeout(r, 450));
    state.job = await request("/jobs/" + state.job.job_id);
  }
  const job = state.job;
  await refresh();
  if (job.status !== "completed") {
    const error = new Error(job.error?.message || "Processing failed.");
    error.code = job.error?.code || "JOB_FAILED";
    throw error;
  }
  render();
  return job.result;
}
async function perform(fn) {
  if (state.busy) return;
  state.busy = true;
  state.cancel = false;
  state.error = null;
  render();
  try {
    await fn();
  } catch (error) {
    state.error = {
      code: error.code || "REQUEST_FAILED",
      message: error.message,
    };
    if (state.project) {
      try {
        await refresh();
      } catch {}
    }
  } finally {
    state.busy = false;
    state.job = null;
    state.operation = "";
    render();
  }
}

export { request, post, refresh, stage, perform };
