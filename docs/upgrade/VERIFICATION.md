# Backend upgrade verification — 2026-10-04

## Scope and execution

Existing ReVector engine extended locally. RevectorAi-WEB has no changes from
this task; its pre-existing preview-race patch and generated assets are preserved.
At verification time, no push, deploy, Railway/Cloudflare configuration change or secret setup occurred.

## Executed checks

- Baseline: 85 engine tests, plus 7 existing gateway tests passed before changes.
- Final engine regression: **159 tests passed**, zero failures/errors/skips;
  suite time **28.988 seconds**. Retained JUnit XML and JSON summary.
- Ruff checks for new AI/error/workflow/contracts modules and export validation passed.
- Synthetic eight-slot acceptance: real CV masks, vectors, render, individual
  SVG/PDF/EPS, shape fill edit, Inkscape object import and parts-only ZIP passed.
  All eight exported SVGs contain zero rasters. AI is an explicitly mocked
  orchestration provider in this acceptance, not a live production provider.
- Real Hellas Verona regression: earlier external-AI raster reference, six
  detected parts, two intentionally blank trim slots; **119 paths, 7,009 anchors,
  zero raster objects**, real SVG render and PDF/EPS parser checks passed.
  Prior recorded pipeline/export time: **12.912 seconds**, zero new AI calls.
- Rasterized PDF is rejected; embedded-raster SVG, malformed/degenerate paths,
  native trace failure/timeout, interrupted jobs, ownership, safe error actions,
  redaction and advisory assistance during a busy stage have regression coverage.
- Infrastructure files and app/server.py have no diff. No frontend upgrade diff.

## Evidence

`samples/upgrade/test-results.xml`, `test-summary.json`, `acceptance.json`,
`validation.json`, `events.json`, individual SVG/PDF/EPS and
`parts-only-production.zip`. The real-source regression is under
`samples/upgrade/real-verona/`, including `strict-export-audit.json`.

`contracts/openapi.json`, typed JSON schemas and deterministic offline catalog
are provided for the separate frontend integration task.

## Acceptance still requiring an equipped runtime

- No live Cloudflare/OpenAI/Gemini credentials were configured. Adapter contracts,
  routing and fallback use mocked HTTP tests; actual AI fidelity, latency and model
  availability remain unverified. Configured is never reported as Connected.
- Adobe Illustrator was unavailable. Strict SVG/resvg/Inkscape/Poppler/Ghostscript
  checks passed; native Adobe acceptance is **NOT_RUN**. Native .AI export is unavailable.
- Future frontend work must add the gateway route allowlist, review flow, offline
  catalog and assistant popup; none was implemented in this backend-only task.
- Strict PDF/EPS checks now require Poppler, plus Ghostscript for EPS. Existing
  container configuration is untouched. Approve runtime dependency/deployment
  changes separately before enabling these formats in production.
- AI-inferred missing artwork, exact branding, fonts, seams and calibrated garment
  measurements still require human review. Eight slots do not fabricate eight masks.

## Five-source follow-up benchmark

After real source testing, palette clustering was refined to preserve small branding
colors using coverage seeding and deterministic LAB iterations, with a versioned
reconstruction cache. Three additional regression tests pass; final engine suite:
162 tests, zero failures/errors. Five real internet jersey references generated
through external image_gen tools yielded 40 individually editable SVG/PDF/EPS
exports, verified by strict parsers and Inkscape. No live engine AI API, native
Adobe test, production deployment or frontend edits. Detailed report retained at
/workspace/revector-five-jerseys-20261004; original color-loss baseline retained.

The five-source narrative is retained in `FIVE_JERSEY_REPORT.md`, with structured
metrics and independent verification in `samples/upgrade/five-jerseys/`.
The tested update is published on `upgrade/ai-production-engine`; the production
`main` branch and Railway/Cloudflare settings remain unchanged.
