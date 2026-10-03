# API contract

OpenAPI: `GET /openapi.json`. Interactive documentation: `GET /docs`.
Both require `Authorization: Bearer <REVECTOR_API_KEY>`. Every `/api/revector`
request also requires `X-Revector-User: <trusted-owner-identity>` from the server-side
gateway. Project/job/artifact access is owner-checked. Browsers use the Cloudflare
website proxy; never expose the engine credential to clients. Missing credentials
return 401; missing engine configuration returns 503. Cross-owner access returns 404.
See [secure connection](SECURE_CONNECTION.md).
All identifiers are server-generated UUIDs. Artifacts use relative storage keys.
Processing errors return a stable `error.code`, public-safe `message` and
`recoverable` flag. Job errors appear in polling responses.

1. `POST /api/revector/projects` with JSON `{ "name": "Jersey", "user_id": "host-user", "settings": { "preset": "BALANCED", "vector_mode": "color" } }`.
2. `POST /api/revector/upload` with multipart `project_id` and `file`. Content-Length is checked when present; streamed/chunked multipart bodies are also size-bounded. Content bytes, MIME and extension must agree.
3. `POST /api/revector/analyze` with `{ "project_id": "UUID" }`.
4. `POST /api/revector/correct-geometry` with the same JSON for identity, or add `"corners": [[x,y],[x,y],[x,y],[x,y]]`. `"auto": true` explicitly authorizes a recorded automatic artboard crop. Coordinates refer to the normalized source.
5. `POST /api/revector/segment`. Optional `parts` is a list of `{ "name": "Front", "type": "front_body", "confirmed": true, "polygon": [[x,y],...] }`. Coordinates refer to the corrected image.
6. `POST /api/revector/segments/{part_id}/confirm` or `/update`, passing `project_id` plus update fields. `locked` prevents changes until explicitly unlocked.
7. `POST /api/revector/reconstruct`, `/vectorize`, `/optimize`, `/compose`, `/validate`, each with `project_id`. Reconstruction, vectorization and optimization optionally accept `part_id`. Composition includes all current parts and requires each to be optimized.
8. `POST /api/revector/export` with `{ "project_id": "UUID", "formats": ["svg","pdf","eps","png","zip"] }` after validation.

Every processing endpoint returns HTTP 202 and a `job_id`. Poll
`GET /api/revector/jobs/{job_id}` until `completed`, `failed` or `cancelled`.
`POST /api/revector/jobs/{job_id}/cancel` requests cooperative cancellation.
One queued/processing job is allowed per project. Synchronous development mode
still returns the completed job object and preserves the same contract.

Read-only endpoints:

- `GET /health`: available dependency capabilities, even when optional tools are missing.
- `GET /api/revector/projects/{id}`: complete current manifest and derived `true_vector_ready`.
- `GET /api/revector/projects/{id}/status`: state, usage and last error.
- `GET /api/revector/projects/{id}/parts`: masks, metadata and per-part metrics.
- `GET /api/revector/projects/{id}/validation`: integrity, factual vector status, compatibility scope and visual comparison.
- `GET /api/revector/projects/{id}/exports`: actually generated formats.
- `GET /api/revector/projects/{id}/artifacts/{relative-path}`: download an artifact present in the current manifest; e.g. `exports/master.svg`.

`PUT /api/revector/projects/{id}/settings` updates supplied fields and invalidates
the appropriate stages. It returns the new manifest; run downstream stages again.
`DELETE /api/revector/projects/{id}` removes the project when no job is active.

`POST /api/revector/parts/actions` takes `project_id`, `action`, and:

| Action | Required fields |
|---|---|
| add | polygon; optional type/name |
| remove | part_id |
| merge | part_id and part_ids of other existing unlocked parts |
| split | part_id and polygons for each resulting component |

Manual edits are recorded with timestamps. Semantic labels are never guessed by
the OpenCV fallback. Side designation must be confirmed by the host UI. This
release supports manual polygons; point-assisted segmentation requires an injected
segmentation provider.

Exports are partial-success aware: if EPS/PDF fails, the export job reports failure
and includes `export_errors` alongside successful files. The validated SVG and
READY project are preserved. ZIP includes only generated files and current parts.
EPS transparency may require visual review; PDF is preferred for gradients.
