# ReVector — Web Workspace & Core Engine

A runnable Python 3.12 deterministic raster-to-vector backend for JerseyOS.
It builds editable paths and groups, validates them, renders them with resvg,
and exports real SVG, PDF and EPS. An API-connected browser workspace is served
by the same FastAPI process. No image-generation API is required.

**An SVG extension does not establish vector artwork.** True Vector export
requires real geometry and zero raster images/references. PNG-in-SVG wrappers
are rejected. Raster-only artwork is INVALID_VECTOR; genuine vectors plus raster
artwork are HYBRID_VECTOR and cannot be exported as True Vector.

This is a tested deterministic core release, not a finished autonomous photo-to-
garment reconstruction system. It does not recover obscured artwork, identify
garment side labels from appearance, or certify print/cut dimensions without
calibration. Its static Illustrator checks are not an Adobe Illustrator runtime
test. Review the measured sample report and limitations below.

## Local installation

```bash
cd revector-engine
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Open **http://127.0.0.1:8000/** for the web tool. API documentation is at `/docs`.
The web interface is included in the Python package and Docker image: no Node
build step, separate frontend server or CORS configuration is required.

Opening `app/web/index.html` directly or in an attachment preview displays the
workspace interface. Actual processing requires opening the page from the running
engine address above. Offline previews show connection guidance instead of waiting
indefinitely. See the web integration notes for rebuilding the bundled HTML after
editing frontend sources.

## Browser workflow

1. Upload raster artwork, or try the bundled jersey/multiple-panel samples.
2. Analyze source quality and detect parts. Automatic perspective is opt-in.
3. Select each component, rename it, assign its category, supply dimensions and
   save/confirm it. Add/redraw polygons or provide four perspective corners when needed.
4. Vectorize: reconstruction, tracing, cleanup, composition and rendered validation
   run as background jobs. Progress and cancellation use the actual engine job API.
5. Compare the clean raster and vector, inspect real layers, or view paths. Select
   a vector shape to change its solid fill; edits require composition/revalidation.
6. Download individual SVG/EPS/PDF files, a selected-parts ZIP, the master or a full
   production ZIP. Downloads are gated by the current validated geometry hashes.

The interface restores the latest project in the same browser. It does not invent
part labels, confidence scores, measurements, validation metrics or successful exports.
OpenAI is explicitly **not connected**; no OpenAI API requests are made. Native
`.AI` remains unavailable without Adobe Illustrator automation.

See [web integration notes](docs/WEB_WORKSPACE.md) for physical sizing, testing
and deployment details. Figma mockup: https://www.figma.com/design/p49n14O798ZY4d3eHpcxVG

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
The engine should be private behind JerseyOS authentication; it deliberately
does not implement host billing or user authentication.

## API workflow

Open `http://127.0.0.1:8000/docs` for exact request models and try-it-out controls.

```bash
curl -s http://127.0.0.1:8000/api/revector/projects \
  -H 'Content-Type: application/json' \
  -d '{"name":"Front artwork","settings":{"preset":"BALANCED","vector_mode":"color"}}'
# Use the returned project_id in each subsequent request.
curl -s http://127.0.0.1:8000/api/revector/upload \
  -F 'project_id=REPLACE_WITH_UUID' -F 'file=@artwork.png;type=image/png'
curl -s http://127.0.0.1:8000/api/revector/analyze \
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
