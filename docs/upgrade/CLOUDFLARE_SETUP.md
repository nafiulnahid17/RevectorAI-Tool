# Cloudflare fallback setup and verification

Use Workers AI directly from the Railway engine. AI Gateway is unnecessary.
Keep all Main AI variables unset. Do not change gateway authentication or the
website repository. Provider configuration belongs exclusively in backend secrets.

## Required configuration

| Variable | Value |
| --- | --- |
| `CLOUDFLARE_ACCOUNT_ID` | The account verified by the operator |
| `CLOUDFLARE_AI_TOKEN` | Persistent Workers AI inference API token; secret |
| `CLOUDFLARE_AI_MODEL` | `@cf/meta/llama-4-scout-17b-16e-instruct` |
| `CLOUDFLARE_AI_IMAGE_MODEL` | `@cf/black-forest-labs/flux-2-klein-4b` |
| `ERROR_AI_PROVIDER` | `cloudflare` |

Leave `MAIN_AI_PROVIDER`, `MAIN_AI_API_KEY`, `MAIN_AI_BASE_URL`, `MAIN_AI_MODEL`
and `MAIN_AI_IMAGE_MODEL` unset. Error Assistant reuses the Cloudflare credentials
and text model; no separate Error AI secret is necessary.

Create a persistent token restricted to the selected account with Workers AI
inference permission. Store it directly in Railway's secret variables; never
paste it into chat, source files, public configuration or reports. A Wrangler
OAuth login verifies account access, but its refresh-dependent credential must
not be copied into Railway as a permanent API token. Verify the persistent token
with an inference request from a trusted operator/runtime environment before
claiming production authentication works.

Deploy the adapter change supporting Scout's native multimodal `messages` and
`response_format` contracts before selecting this model. Legacy Cloudflare
models retain their existing request format. Scout identification uses named
`x`, `y`, `width`, `height` wire fields to distinguish extents from right/bottom
coordinates. These convert into the existing public bounding-box tuple without
clamping, moving or inventing coordinates. Existing router and CV validation
remain responsible for acceptance and exact production boundaries.

## Local live evidence — 2026-10-04

These are real Cloudflare inference calls authenticated through Wrangler-managed
OAuth. The engine's request/response contracts and router were used locally;
the authentication transport alone was replaced by Wrangler's authenticated
client. These results do not verify a Railway API token or deployed fallback.
Separate automated tests use explicit mocks.

| Operation | Result | Duration |
| --- | --- | --- |
| Scout artwork analysis, small jersey image | Valid structured response; `fallback_ai`, `cloudflare` | 8.464 s |
| Scout Error Assistant | Real AI response, allowlisted advice | 6.088 s |
| Scout semantic part identification after named-box change | Four schema-valid candidates; `fallback_ai`, `cloudflare` | 11.884 s |
| FLUX.2 Klein 4B reference-image edit, two-color fixture | HTTP 200; decodable 512 × 512 PNG, 26,948 bytes | 2.840 s |
| FLUX.2 Klein 4B jersey reference-image edit | HTTP 400, code 3030: provider flagged generated output | 2.813 s |

Identification is a contract smoke test, not proof of eight accurate pieces.
The live model assigned overlapping front/back candidates; CV refinement and
user review are still required. No geometry or garment part was fabricated to
make the model appear successful. Earlier responses with invalid coordinate
extents were rejected, and the native request schema was corrected without
weakening the validator. Legacy Llama 3.2 responses did not reliably satisfy the
engine's structured JSON contract; Scout was selected from the live model catalog.

Image capability success on a fixture does not establish jersey reconstruction
quality. Provider refusal must remain a structured failure; do not silently
switch models, label a raster mockup vector or claim an eight-part production
layout was successfully generated.

## Deployment verification

After the persistent token and model configuration are applied and the correct
adapter revision is deployed:

1. Verify the Railway deployment revision and `SUCCESS` state.
2. Check `/health` and `/health/ready`.
3. Query authenticated `/api/revector/capabilities/ai`; configuration is not
   evidence of successful inference.
4. Run a real small artwork analysis. Check actual operation metadata:
   `processing_mode=fallback_ai`, `provider=cloudflare` and the selected model.
5. Verify real Error Assistant advice and supported actions, not a local-catalog
   response incorrectly labelled AI.
6. Run a bounded image-capability test where practical and retain refusals.
7. Verify Main AI remains unconfigured and deterministic readiness still passes.
8. Ensure response/log summaries contain no credentials.

The API key, user ownership checks, vector validators and frontend gateway
remain unchanged. The Cloudflare setup does not validate Adobe Illustrator
editability; that remains the deterministic vector/export pipeline's job.
