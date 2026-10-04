# AI-assisted production upgrade

Incremental changes to the existing engine only. The website repository remains unchanged by this task.
No production deployment or infrastructure changes have been performed.

## Configuration

Set secrets only in Railway/backend configuration. Browser/static files contain
none of these values. `.env.example` contains empty credentials.

| Variable | Purpose |
|---|---|
| MAIN_AI_PROVIDER | Empty, openai, gemini, openrouter, custom, compatible, or cloudflare |
| MAIN_AI_API_KEY | Primary provider credential |
| MAIN_AI_BASE_URL | Optional HTTPS base URL, required for custom/compatible |
| MAIN_AI_MODEL | Structured text/vision model |
| MAIN_AI_IMAGE_MODEL | Reference-edit image model, separate capability |
| CLOUDFLARE_ACCOUNT_ID | 32-character account id |
| CLOUDFLARE_AI_TOKEN | Server-only Workers AI token |
| CLOUDFLARE_AI_MODEL | Vision/text model; e.g. @cf/meta/llama-3.2-11b-vision-instruct |
| CLOUDFLARE_AI_IMAGE_MODEL | @cf/black-forest-labs/flux-2-klein-4b or flux-2-klein-9b |
| ERROR_AI_PROVIDER | cloudflare by default; same adapter registry as artwork AI |
| ERROR_AI_API_KEY | Optional independent error-assistant key |
| ERROR_AI_BASE_URL | Optional HTTPS endpoint |
| ERROR_AI_MODEL | Error text model; defaults to Cloudflare model when Cloudflare selected |
| AI_TIMEOUT_SECONDS | Individual provider network timeout, default 120 |
| REVECTOR_JOB_TIMEOUT_SECONDS | Cooperative job budget, default 1800 |

Blank primary → Cloudflare immediately. Failed primary or schema-invalid response
→ Cloudflare for that operation. If both fail, a normalized error stops AI
progression. If no AI provider is configured, deterministic/manual preparation
works and reports AI not used. A provider is identified as processing_mode
primary_ai/fallback_ai only after a real response passes its contract.
There are no engine daily/credit fallback quotas. Provider billing/rate limits
remain outside ReVector's control. No live provider credentials were available for
acceptance testing; HTTP contract tests use explicit mocks, not production claims.

Cloudflare reference-edit contract documented at
https://developers.cloudflare.com/changelog/post/2026-01-15-flux-2-klein-4b-workers-ai/ .
Multipart `input_image_0` receives a resized copy below 512px; original resolution
is retained in source storage. This provider limitation can lose small branding.
Vision contract documented at
https://developers.cloudflare.com/workers-ai/models/llama-3.2-11b-vision-instruct/ .
An account may need to accept the model license before inference; ReVector never
automatically accepts legal terms. OpenRouter/custom must actually support the
configured OpenAI-compatible `/chat/completions` and `/images/edits` APIs. Merely
selecting a provider does not imply image capability. Unsupported image models or
responses fail explicitly and route to fallback. Gemini uses generateContent and
inline image bytes. Redirects/remote generated image URLs are never followed.

## API workflow

Existing authenticated API routes and deterministic CLI remain available.
New owner-checked routes:

1. Upload with optional multipart `auto_prepare=true` to queue preparation and
   return preparation_job. Legacy upload remains unchanged; then explicitly call
   `POST /api/revector/prepare {project_id}`.
   Automatic analysis → enhancement → centralized versioned mockup → semantic
   identification → CV mask/contour/crop refinement → PART_REVIEW_READY.
2. Inspect `project.slots`: exactly eight logical slots; physical components may
   be fewer, more or uncertain. Do not equate a prompt's requested count with CV
   proof. No mask is fabricated to fill a slot. AI confidence is nullable model
   evidence; component overlap is measured by CV, not an invented confidence.
3. Rename/reclassify/redraw via existing manual API. Each part must be confirmed.
   `POST /slots/update {project_id,slot,status:"blank"}` for intentionally absent
   pieces. `POST /ai-missing {project_id,slot}` makes an inferred raster proposal,
   isolates it with CV, and pauses for review. Manual polygons use the same
   reconstruction/tracing/validation pipeline as AI-refined parts.
4. `POST /review/confirm {project_id,decisions:{FRONT_BODY:{status:"confirmed",
   part_id:"..."},BACK_BODY:{status:"blank"},...}}`. All eight slots must resolve,
   all nonblank pieces match their type and are confirmed, no extra components
   remain. This endpoint automatically queues production.
5. Production reconstructs/traces/optimizes each part independently, composes an
   internal validation layout, validates grammar/resources/true vectors, renders,
   compares and validates individual SVGs. PASS emits EXPORT_READY for the future frontend to show Download.
6. `POST /recover-part {project_id,part_id,fallback_trace:true}` retries only that
   part using deterministic contours and then composes/revalidates the project.
   Other successful vector artifacts and caches remain unchanged. Unresolved
   sibling failures prevent project approval/download.
7. `POST /export {project_id,part_ids:[...],formats:["svg","eps","pdf","zip"]}`.
   Omit part_ids to export all individual parts. ZIP never contains master/full
   patterns, assembled diagnostic SVGs or assembled previews. Only actually
   generated individual files, individual previews and metadata are packed.

Eight slots: LEFT_SLEEVE, RIGHT_SLEEVE, FRONT_BODY, BACK_BODY, FRONT_COLLAR,
BACK_COLLAR, TOP_TRIM, BOTTOM_TRIM. Blank is a user decision, not a fake component.

Project events: `GET /projects/{id}/events`. Error history:
`GET /projects/{id}/errors`. Job polling preserves legacy lowercase `status`
(queued/processing/completed/failed/cancelled) and adds the explicit uppercase
`job_state` (QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED), part_id, started_at,
updated_at, completed_at, normalized error and current process_event. Network
calls have finite timeouts. Cancellation/job timeout are checked at safe
boundaries; VTracer/Potrace and export subprocesses have tool timeouts. Python contour/cleanup
work remains cooperative at safe boundaries, rather than force-killed midway. Worker restart honestly marks interrupted jobs FAILED.

## Global advisory assistant

Backend exceptions/jobs/gateway/browser errors have normalized error ids, category,
phase, safe message, retryability and allowlisted actions. The backend exposes a deterministic catalog and advisory API. The future website
must bundle the documented local catalog to show guidance when offline or when
Railway is unavailable, and must not label local guidance AI. Online help is optional:

`POST /assistant/explain {project_id,error_id,question}`

For browser/network errors, a bounded `error` object can be supplied. The service
projects only safe fields, selected validator metrics, bounded question/history,
and recovery history. Never sends raw projects, environment, headers, cookies,
credential fields, arbitrary diagnostic dictionaries or private image files.
Configured secrets and credential-like strings are redacted. Sessions retain six
messages, at most ten error sessions, omitted from public manifests/packs.
Advisory help remains available during a running stage. If the project lock is
busy, the response reports memory_persisted:false and avoids modifying the
in-flight manifest; the future client may retain that conversation locally.

AI response actions must be both globally allowlisted and supported for the
specific error. Unknown actions reject the response and show local guidance.
There is no arbitrary action executor. Future buttons must invoke existing typed, authorized
APIs and require a user's click. AI cannot mark validator PASS, delete projects,
modify credentials or change infrastructure. Cause claims from the model are
hypotheses; local diagnostics identify known facts. Failed assistant inference
never crashes processing. `POST /assistant/feedback` records an allowlisted action
and user-observed SUCCESS/FAILED/CANCELLED; this never trains external providers.

## Compatibility and review

Master SVG remains an internal composition/validation artifact; its download route
is denied. Native .AI remains explicitly unavailable. SVG is canonical. EPS/PDF
use actual Inkscape conversion and are subsequently parsed: Poppler pdfinfo/pdfimages
checks PDFs; Ghostscript parses EPS into an audit PDF for raster inspection.
True Vector conversion containing raster objects is rejected. Poppler tools and
Ghostscript must be available before EPS/PDF exports are enabled; missing tools
return an export-specific failure and preserve SVG. Deployment dependencies need
review/approval; the existing Dockerfile/Railway configuration is untouched. Resvg/strict parsing and Inkscape/Ghostscript are
acceptance equivalents here; Adobe Illustrator was not installed/executed.

A valid vector does not establish accurate branding, seam alignment, typography,
physical sewing measurements or original unseen artwork. Black-on-black edges,
touching parts, disconnected small motifs and partial photographs can require
manual geometry. AI-inferred backs/trims are proposals. Only supplied measurements
calibrate parts. The prompt never manufactures real-world production dimensions.

## Local checks and deployment preparation

Engine: `.venv/bin/pytest` (or install `.[trace,r2,dev]` then `pytest`).
Frontend integration is explicitly out of scope. This backend is tested through
FastAPI TestClient with ownership/security and HTTP contracts.
Run `scripts/verify_upgrade.py` for retained eight-part SVG/PDF/EPS/ZIP artifacts
and strict parser/render/import checks. This uses a clearly labelled mocked AI
provider to exercise orchestration; CV/vector/export/render operations are real.

Railway Dockerfile/startup/volume/auth configuration are preserved. httpx is now a
runtime dependency. Use one API process/replica for the current queue. The future Cloudflare
Worker task must allowlist the new APIs without exposing provider credentials.
Production deploy, repository push with auto-deploy, or configuration changes
require explicit approval; none is included in this implementation.
