# ReVector Core Engine

Python 3.12 raster-to-vector API for JerseyOS. This repository contains **only the
engine**, processing stages, validators, exports, CLI and backend tests.

The website is maintained separately in
[RevectorAi-WEB](https://github.com/nafiulnahid17/RevectorAi-WEB) and runs on
Cloudflare Workers. The engine runs on Railway or another Python/Docker server.
The browser calls its own Cloudflare origin; only the server-side Worker holds the
engine credential and connects to the engine over HTTPS.

**An SVG extension does not establish vector artwork.** True Vector requires real
geometry, valid SVG, rendered validation and zero raster images/references.
No fake `.ai` files or PNG-in-SVG wrappers are exported. OpenAI is not connected;
the deterministic pipeline does not require an AI API key.

## Local installation

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
# Set a random REVECTOR_API_KEY of at least 32 characters in .env.
uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

`/health` is public liveness; `/health/ready` performs actual readiness checks.
API routes and developer documentation require the trusted gateway bearer key.
Project/job/artifact requests also require `X-Revector-User`, supplied by the trusted
server-side gateway. Every referenced project must belong to that identity.
Missing configuration fails closed. No permissive cross-origin browser access is
provided. For isolated local debugging only, `REVECTOR_ALLOW_UNAUTHENTICATED=true`
explicitly disables gateway authentication; do not set it on a deployed engine.

See [secure connection](docs/SECURE_CONNECTION.md) and [Railway setup](docs/RAILWAY.md).

On Debian/Ubuntu, install optional production conversion and OCR tools:

```bash
sudo apt-get update
sudo apt-get install -y inkscape potrace tesseract-ocr fonts-dejavu-core
```

OpenCV and resvg are Python dependencies; VTracer is included by requirements.
Missing optional trace/conversion tools are reported by `/health`. Contour tracing
works without VTracer/Potrace; PDF/EPS requires Inkscape. Tool failures never create
fake extensions or replace vectors with raster data. On Windows, use Docker/WSL
because the local project-lock implementation uses Linux `fcntl`.

## Docker

```bash
docker compose up --build
curl http://127.0.0.1:8000/health
```

Docker runs as a non-root user with persistent local storage and one API process.
See [architecture](docs/ARCHITECTURE.md) for the local queue's deployment limits.

## Railway

Deploy this repository as a Docker service with a persistent `/engine/data` volume,
set `REVECTOR_API_KEY` as a Railway secret, and generate an HTTPS domain after
`/health/ready` passes. `PORT` is read automatically. One replica and one worker are
required by the local queue. This service serves the API, not website files.

## API workflow

Developer documentation is at `/docs` and requires bearer authentication.
See `docs/API.md` for the static API contract. Set ENGINE_API_KEY in your trusted
operator environment for the examples below; never put it in browser code.

```bash
curl -s http://127.0.0.1:8000/api/revector/projects \
  -H "Authorization: Bearer $ENGINE_API_KEY" -H "X-Revector-User: operator-local" \
  -H 'Content-Type: application/json' \
  -d '{"name":"Front artwork","settings":{"preset":"BALANCED","vector_mode":"color"}}'
# Use the returned project_id in each subsequent request.
curl -s http://127.0.0.1:8000/api/revector/upload \
  -H "Authorization: Bearer $ENGINE_API_KEY" -H "X-Revector-User: operator-local" \
  -F 'project_id=REPLACE_WITH_UUID' -F 'file=@artwork.png;type=image/png'
curl -s http://127.0.0.1:8000/api/revector/analyze \
  -H "Authorization: Bearer $ENGINE_API_KEY" -H "X-Revector-User: operator-local" \
  -H 'Content-Type: application/json' -d '{"project_id":"REPLACE_WITH_UUID"}'
```

Poll the returned job id, then proceed through geometry, segmentation,
reconstruction, vectorization, optimization, composition, validation and export.
See [API documentation](docs/API.md) for the full contract, correction endpoints,
per-part processing, artifact downloads and cancellation.

## CLI

```bash
revector analyze artwork.jpg
revector segment artwork.png
revector vectorize artwork.png --mode balanced --strategy color --output output
revector run artwork.png --formats svg pdf eps png zip --output output
revector validate output/master.svg --render
revector export output/master.svg --format eps --output output/master.eps
```

`--mode` selects FAST/BALANCED/PRECISION/ULTRA; `--strategy` selects precision
contours, VTracer color tracing, Potrace monochrome tracing, or deterministic
component reconstruction. Presets change actual trace parameters and simplification
tolerance. BALANCED is the default. The source resolution is preserved unless
`max_trace_dimension` is explicitly configured; that scaling is recorded.

For phone/screenshot input, supply `--corners corners.json` to rectify an explicit
quadrilateral. Supply `--parts parts.json` to use confirmed manual polygons.
Automatic segmentation defaults to unknown parts and requires boundary review.

## Vector exports and Illustrator

The canonical master SVG contains individual editable paths and standard named
groups. VTracer paint order is preserved. Physical production lines are separate
hidden CUT_PATH/BLEED_PATH/SAFE_ZONE groups. Set `known_width_mm` to calibrate the
whole corrected artboard width before using millimetre offsets. No garment sizes
are invented. A diagnostic SVG displays path structure; nodes JSON supplies local
path endpoints and commands for a future editor.

PDF/EPS uses Inkscape conversion with safe argument arrays, timeouts and output
signature checks. No `.ai` file is fabricated. Static standard-SVG compatibility
is reported as PASS/PASS_WITH_WARNINGS/FAIL; actual Illustrator import remains a
manual application test. EPS gradient/opacity handling should be inspected; PDF
is generally preferable. This release neither assigns CMYK/spot colors nor performs
ICC color matching, printer profiling, seam layout or material compensation.

Hybrid reconstruction means separating components before tracing; it still creates
vectors. The validator recognizes explicitly provided hybrid artwork, but this
release never generates embedded raster layers, even if export_mode is hybrid.
Changing export_mode cannot bypass the raster prohibition for True Vector exports.

## Tests and samples

```bash
python scripts/generate_fixtures.py
pytest
revector validate samples/rejected-fake.svg
revector validate samples/attached-front/master.svg --render
```

Fixtures cover two-color graphics, gradients, splatter, outlined text, logo holes,
low resolution, perspective, shadows, complex panels and multiple components.
Tests cover upload→export, real backend behavior, XML/raster regressions, cache
invalidation, manual corrections and actual Inkscape conversion when installed.
The fake SVG validation command intentionally exits with status 1.

The sample input is the supplied **UI screenshot**, not an original production
artboard. A recorded crop selects the front artwork shown in its left viewport.
See `samples/attached-front/verification.json` for executed measurements and
`samples/attached-front/validation.json` for vector and rendering validation.
The sample pack includes the source-derived vector, PNG previews and actual
successful export files. Raster reference and preview PNGs are explicitly labeled
intermediates/previews, never presented as vector masters.

## Current limitations and extension points

- Deterministic color/alpha/contour masks cannot reliably separate every photograph
  from clutter. Manual polygons and explicit corners are the dependable fallback.
- Cleanup preserves graphic edges but does not restore missing information or
  reconstruct severe glare/shadows. Original source and clean reference are retained.
- Color quantization simplifies irregular photographic gradients and textures.
  A conservative global linear-gradient fit uses measured RGB residuals; localized
  gradients, radial gradients and repeating-pattern reconstruction are not implemented.
- Curves produced by VTracer/Potrace are retained; bounded line simplification,
  small-component removal, same-order duplicate removal and self-intersection
  reporting run without claiming optimal Bezier anchor reduction.
- Tesseract OCR is optional and records actual model confidence. Text is safely
  outlined by the same vector tracing. Editable-font replacement is NOT_IMPLEMENTED,
  because font substitution and raster text removal require additional evidence.
- SAM/PaddleOCR/YOLO and AI providers have injectable protocols, but pretrained
  adapters are not bundled. An unsupported configured provider fails explicitly.
- R2Storage implements real object operations; automatic project mirroring is
  NOT_IMPLEMENTED. The queue is a bounded thread worker with disk polling records,
  not a durable Redis/Celery worker. Multiple API processes/replicas are unsupported.
- Visual metrics are advisory. A valid True Vector result can still be a poor
  reconstruction; compare the full-size reference/vector/difference previews.
- This release needs operational load testing and an actual Adobe Illustrator
  import/edit session before being certified for a production deployment.
