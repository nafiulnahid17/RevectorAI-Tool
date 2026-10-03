# Web workspace integration

The browser client lives in `app/web`. FastAPI mounts it after the API routes.
Its relative `/api/revector` URLs use the same host as the engine. Every processing
button submits a real job and polls its persisted status. Errors retain machine
codes. No fixture vectors or invented results are substituted for uploaded artwork.

## HTML previews and connection status

The shipped `app/web/index.html` contains CSS, bundled JavaScript, a favicon and
initial markup generated from the same UI sources. Even script-restricted previews
show the interface. Interactive controls and checks require JavaScript.

The frontend shows small Server / Engine / Tool lights. All real checks must pass
before Ready appears and upload is enabled. Pending lights pulse, failed lights
are red, and Retry is shown only on failed segments. There is no setup/retry banner.
See `RAILWAY.md` for what each segment checks. Each connection request times out
within five seconds; a retry makes actual requests rather than changing UI flags.

An attachment preview is not a Python server. To process artwork, open the running
engine domain or `http://localhost:8000/` on its machine. Sandboxed storage failures
do not prevent the interface from rendering or processing when the engine is reachable.

Modular UI sources remain editable. After changing `app/web/*.js`,
`workspace.css` or `workspace.template.html`, regenerate the committed HTML:

```bash
python scripts/build-workspace.py
```

This development-only build invokes pinned `esbuild@0.25.12` through `npx`.
It requires Node/npm and downloads the build tool on first use. An installed
executable can be passed with `--esbuild /path/to/esbuild`. Running the packaged
engine or Docker image does not require Node or rebuilding the UI.

## Measurements and vectors

Project creation accepts optional `production_specifications` entries:

```json
{"name":"Front body","type":"front_body","width_mm":520,"height_mm":720}
```

These are requirements, not detections. The user explicitly matches a requirement
to an actual detected component. Updating a part accepts `physical_width_mm`,
`physical_height_mm`, `bleed_mm`, `safe_zone_mm`, category and confirmation. Both
physical dimensions must be present or absent. Aspect lock in the UI derives
height from the detected crop; disabling it permits explicit width/height sizing.
Unequal physical and raster aspect ratios change the artwork's printed proportions.

Individual part SVGs use actual `mm` dimensions and a pixel viewBox; the supplied
width and height are applied with `preserveAspectRatio="none"`. Offsets are buffered
in physical space and stored in separate hidden production geometry groups. Bleed
and safe-zone lines are guides, not synthesized artwork outside a source boundary.
The master retains the source layout. Its real-world calibration uses the existing
project `known_width_mm`; per-part measurements are recorded in master metadata,
and authoritative physical individual files are included in the production pack.

Export accepts `part_id` or `part_ids` and SVG/PDF/EPS/PNG/ZIP formats. A selected-parts
ZIP contains only selected part files and the project reports; it omits the full
master. A full pack contains all current part SVGs, generated part conversions,
available master files, previews, metadata and validation. Unavailable conversions
raise export errors and preserve successful files. PDFs/EPS are real Inkscape outputs.

Solid color edits use `POST /segments/{part_id}/vector-edit` with `project_id`,
`shape_id`, and a six-digit hex `fill`. The backend validates real geometry, records
the edit and invalidates composed outputs. It cannot inject arbitrary SVG, scripts,
remote assets or path commands through this endpoint. Nodes are edited in Illustrator;
the current browser provides shape fill edits, visibility inspection and path preview.

## Run and deploy

Follow the README installation or run `docker compose up --build`, then open
http://127.0.0.1:8000/ on that machine. For a server deployment, route the same
origin's `/` and `/api/revector` to this single service, persist `/engine/data`, and
put JerseyOS authentication in front of it. The provided development compose binds
to loopback; expose it through your server's reverse proxy when deploying.

Use one API process with the current local queue. This implementation was run and
tested locally; a hosted public endpoint has not been deployed. No OpenAI key was
generated or saved. Providers are still optional injection points in the core.

## Browser verification

`scripts/browser-smoke.cjs` uses Playwright and a Chromium executable. With the
server running, install Playwright in your development environment and run:

```bash
npm install --no-save playwright
CHROMIUM_PATH=/usr/bin/chromium node scripts/browser-smoke.cjs
CHROMIUM_PATH=/usr/bin/chromium node scripts/browser-preview-smoke.cjs
```

The script uploads a two-panel raster, analyzes and segments it, confirms dimensions,
traces and validates, changes one shape fill and revalidates, downloads individual
SVG/PDF and selected/full ZIPs, and checks mobile overflow and browser errors. It
saves screenshots, real downloaded files and factual metrics in `samples/web-workspace`.
Backend tests cover sizing, exports, stale hashes, edit invalidation, package serving,
artifact access and full/selected ZIP integrity.
The preview regression script checks opaque-origin HTML rendering without external CSS/JS,
offline size-sheet controls, independent segment failures, retries only on failure,
stalled health timeouts, script-restricted previews and mobile overflow.

The live interface follows the saved editable Figma mockup. The Starter plan's MCP
quota prevented a fresh design-context fetch, so a pixel-exact Figma comparison
was unavailable. Saved design construction records and the previously inspected
layout were used; actual artwork, parts, layers and metrics now come from the engine.
