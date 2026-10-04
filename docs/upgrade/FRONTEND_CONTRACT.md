# Future web integration — APIs only, no UI changes in this task

`RevectorAi-WEB` source/build/security state is restored to its pre-task state.
Do not deploy either repository before approval. The old Worker does not yet
allowlist new endpoints; add them in the separate frontend task. Gateway secrets,
CSRF Origin checks, signed HttpOnly sessions and engine ownership checks remain
required. Provider credentials belong exclusively to the engine/server.

## Orchestration

Upload multipart with `auto_prepare=true` to queue preparation immediately and
poll the returned preparation_job.job_id. Legacy upload omits this field; then
submit `/prepare` explicitly and poll `/jobs/{id}`. Never submit both. At
PART_REVIEW_READY show all eight `project.slots` and current `project.parts`.
Use exact `bbox`, polygon/mask/crops from the engine, never turn AI candidates into
frontend crop geometry. Candidate bbox is normalized [x,y,width,height]; engine
bbox is pixel [x,y,width,height] in corrected_image coordinates. `polygon` points
are absolute corrected-image pixels. `mask` is local to that crop. Project
geometry.output_dimensions gives corrected reference size. No inferred inches/mm.

Missing actions:

- **Create with AI:** POST `/ai-missing {project_id,slot}` → poll → review proposal.
- **Manually select:** POST `/parts/actions {project_id,action:"add",type:
  "left_sleeve",name:"Left Sleeve",polygon:[[x,y],...]}` → existing part update
  and confirmation endpoints. Same downstream deterministic production pipeline.
- **Leave blank:** POST `/slots/update {project_id,slot:"LEFT_SLEEVE",status:"blank"}`.

After all real parts are named/category-confirmed and slots confirmed/blank,
POST `/review/confirm` with decisions for all eight keys. This starts production
without a second vectorize click. A job FAIL stops progression; SUCCEEDED with
VALIDATION_PASSED/EXPORT_READY moves to Download. No fake timers or progress.
Uncertain/duplicate slots cannot be approved without explicit correction.

`GET /projects/{id}/events` returns bounded recorded events. Polling a running job
also includes `process_event` and current `part_id`. Names:
UPLOAD_RECEIVED, ANALYZING_ARTWORK, ENHANCING_ARTWORK, CREATING_PATTERN_MOCKUP,
IDENTIFYING_PARTS, REFINING_PART_BOUNDARIES, PART_REVIEW_READY,
RECONSTRUCTING_PART, TRACING_VECTOR, OPTIMIZING_VECTOR, VECTOR_READY,
VALIDATING_VECTOR, VALIDATION_PASSED, VALIDATION_FAILED, EXPORT_READY,
ERROR_OCCURRED, ERROR_ASSISTANT_READY. Only events for real executed stages appear.
Path sanitation/cleanup is part of the trace/optimize call; do not display a fake
CLEANING_GEOMETRY timestamp when the backend did not separately record one.

Uppercase job_state is authoritative. Lowercase status is retained for existing
clients. Job timeouts are cooperative; never assume an already-running native
trace was terminated immediately. Cancel via `/jobs/{id}/cancel` and keep polling
for a terminal state. Finite frontend poll deadlines should show local guidance,
not silently launch duplicate work.

## Error popup and safe actions

Consume the normalized error object from requests, job errors, or project history.
Show immediate local help. Bundle `contracts/offline-catalog.json` with frontend
static assets: fetching it from an unavailable engine cannot provide offline help.
No-Internet, timeout, engine-unavailable and upload errors need local explanation
without waiting for AI. Label local guidance `source=deterministic_catalog`.

Online assistance: POST `/assistant/explain` with project_id,error_id,question.
For a network/client error with no stored id, provide the strict bounded `error`
object. Follow-up requests reuse the returned error_id. Memory is limited to six
messages/ten error sessions per project. A busy production manifest does not
block advice: memory_persisted:false means the client should retain that turn
locally and resubmit context later if needed. Response `advice.source=ai` includes the
actual provider; otherwise it is local catalog guidance. An AI outage does not
replace the actual project error. There is no arbitrary execution endpoint.

Render actions only if present in `advice.supported_actions` and in your UI's
implemented action map. Unknown action IDs must never execute. Current backend
recommendations use these supported actions:

| ID | Implementation |
|---|---|
| retry_stage | Resubmit the recorded typed stage request, retaining its real params; do not guess params |
| retry_part | POST /recover-part with the failed part_id |
| use_fallback_trace | POST /recover-part with failed part_id, fallback_trace:true |
| open_manual_editor | Local navigation to selected part boundary/shape editor |
| return_to_detect_parts | Local navigation to part review; if no corrected image exists, explicitly run deterministic prepare with ai_workflow:false |
| reselect_part | Local navigation to boundary selection |
| revalidate | POST /validate; download remains blocked until backend PASS |
| view_validation_report | GET /projects/{id}/validation |
| view_error_details | Show sanitized normalized diagnostics |
| refresh_connection | Rerun local/server/engine readiness probes |
| restart_upload | Open file selection; require explicit user upload |

Reserved allowlisted ids retry_job/use_fallback_ai are not recommended by this
release because no dedicated executable contract is implemented. Never invent
buttons just because an id is globally allowlisted. Destructive operations,
credential changes and infrastructure actions are never assistant actions.

Use `/assistant/feedback {project_id,error_id,action,outcome}` after observed
SUCCESS/FAILED/CANCELLED. The service only records allowed actions. Never call a
failure fixed because advice was shown. UI click permission does not bypass
normal authentication/ownership or deterministic validation.

## Shapes, colors, validation, exports

Use the real part SVG object ids; GET the composed `vectors/{part_id}.svg` from
its current manifest. POST `/segments/{id}/vector-edit {project_id,shape_id,fill:
"#rrggbb"}` modifies actual SVG, invalidates validation/exports, preserves ids.
Then `/compose` and `/validate`, or `/production` for automatic cached stages.
Never change preview only. Each part has a diagnostic `previews.vector_view`.

Validation reports expose status PASS, vector_status TRUE_VECTOR, embedded_rasters,
vector_paths, editable_objects, geometry_integrity, Illustrator static
compatibility, warnings/errors, rendering and visual comparison. Adobe application
acceptance is not claimed. Native AI capability is false.

Export selected/all **individual** parts via `/export`; job.result.part_files maps
part ids to real SVG/EPS/PDF/PNG keys. job.result.exports.zip is the actual
parts-only ZIP. No master/full-pattern buttons. Omitted selection means all
individual parts, not an assembled layout. Internal master and assembled diagnostic
SVGs are not accessible through artifact download. SVG/EPS/PDF must not be renamed
.ai. Warn when supplied dimensions are absent or fidelity needs review.

Schemas are in `contracts/*.schema.json`; the complete API is in
`contracts/openapi.json`; regenerate with
`python scripts/export_upgrade_contracts.py`. None includes provider settings.

AI capability availability: GET /capabilities/ai returns configured booleans,
connection_verified:false and native_ai_export:false. Configured is not Connected.
Actual provider use comes from successful per-operation metadata. GET /error-catalog
provides the same static catalog; the future UI must bundle it for offline use.
