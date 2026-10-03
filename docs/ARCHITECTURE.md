# ReVector architecture

The source image is preserved byte for byte, while a full-resolution EXIF-normalized
RGBA PNG becomes the working image. A manual or explicitly requested automatic
homography produces a recorded corrected image. No automatic crop is applied by
default. OpenCV foreground masks or manual polygons identify unknown components;
garment names require user evidence or an injected model.

```
source → normalize → measured analysis → recorded homography
  → segmentation → component clean reference + quantized source
  → gradient fit / VTracer / Potrace / color contours
  → bounded line simplification + duplicate cleanup
  → grouped master and individual parts
  → safe XML / resource / raster / geometry checks
  → resvg rendering → reference comparison
  → validated SVG, Inkscape PDF/EPS, PNG and ZIP
```

`app/core/engine.py` coordinates independently reusable pipeline modules. The API
uses dependency injection through the application factory. Provider protocols
separate optional OCR, vision, segmentation and image reconstruction from geometry
and validation. They never approve vector validity. Failed optional providers
produce warnings and deterministic operations continue where possible. Provider
implementations must not invent confidence values.

LocalStorage stores atomic project JSON and artifacts under
`projects/{UUID}/{source,working,parts,vectors,previews,exports,reports}`. Advisory
file locks prevent concurrent engine mutation on Linux. Public clients receive
relative object keys, never internal absolute paths. Original sources remain
available after replacement; the current manifest exposes only current artifacts.
Deletion removes the project's files; job audit records remain outside the project.

Stage signatures include settings, dependencies and engine version where relevant.
Independent component signatures permit a single part to rerun reconstruction,
vectorization and optimization while other parts retain their vectors. A changed
boundary invalidates that part and all composed previews, validation and exports.
Settings changes invalidate the earliest affected stage. Changing requested export
formats performs no image analysis. Master SHA256 ties export permission to the
exact validated file. Inputs and outputs are cached on local trusted storage; this
is not a tamper-resistant artifact store.

The thread worker runs CPU/native work away from FastAPI's event loop and records
job state to disk. A bounded queue accepts up to 32 different projects. Only one job
per project is admitted. Native trace calls cannot be interrupted; cancellation
is checked at component and stage boundaries. Worker restart marks unfinished
jobs failed, so callers can resubmit. This release requires **one API process**.
Multiple replicas and durable retries require a shared queue implementation.

Processing states are explicit. READY requires XML and vector integrity,
compatibility checks, successful rendering and stored advisory visual metrics.
`true_vector_ready` additionally requires zero rasters and real paths. Visual
fidelity is advisory: this release does not authorize garments for manufacture
without human review or physical calibration.

SVG master groups identify actual user-confirmed/unknown parts and contain
CUT_PATH, optional BLEED_PATH and SAFE_ZONE, BASE_COLOR, GRADIENT, PATTERN,
SIDE_GRAPHICS, LOGO, TEXT and DETAILS. Empty groups remain empty. Trace paint order
is preserved inside DETAILS, or GRADIENT for a measured smooth gradient fit. This
release does not claim semantic logo, side-artwork or splatter-group recognition.
Cut/bleed/safe lines are hidden by default in artwork exports and shown in the
diagnostic SVG. Every artwork object is editable vector geometry.

The R2Storage adapter implements real S3-compatible object operations. Processing
and job records currently use local disk; automatic R2 mirroring and remote project
orchestration are not implemented. Setting an unsupported backend fails clearly.

The engine is intended to run on a private network behind JerseyOS. The host must
authenticate requests, authorize every project and job id, enforce per-user limits,
and handle billing. `user_id` and `request_id` are validated length-limited metadata,
not credentials. Usage records expose durations, input megapixels, provider calls,
output complexity and export operations without billing coupling.
