# Production upgrade audit — 2026-10-04

Inspected both existing repositories before implementation: API schemas/routes,
project persistence, file locks, upload guards, gateway sessions/CSRF/ownership,
thread queue/restart/cancellation, reconstruction/palette, CV segmentation,
VTracer/Potrace/contour trace, optimization, composition, SVG/raster validation,
resvg comparison, export conversion, CLI, tests and Railway/container startup.

Baseline: 85 engine tests and 7 gateway tests passed. Existing uncommitted
VTracer-empty-path regression fix and web preview-race fix are preserved.
No provider credentials are configured in the managed runtime. No live AI calls,
remote changes, deployment or infrastructure mutation are part of this upgrade.

Existing engine is retained. Add an orchestration layer rather than replacing CV.
Single process/thread queue is still the supported runtime; a killed worker cannot
resume a native trace. Job restart must report FAILED. Cancellation is cooperative.

Security: browser → same-origin Worker → authenticated owner-checked engine.
No public provider secrets. SVG remains fail-closed; rendering is mandatory.

Intentional contract change: public exports and packs become individual-part-only.
Internal layout composition remains for validation/preview; master artifacts are
not public downloadable exports. Tests asserting old master downloads must change.

AI identity is advisory, geometry is CV/manual. Eight logical slots do not imply
eight actual masks. Generated/missing pieces require human review. Image model
fidelity, fonts, seam continuity and actual Adobe acceptance cannot be certified
by mocked provider tests. Native AI export remains unavailable.
