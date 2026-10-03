# Railway deployment

Deploy the repository `nafiulnahid17/RevectorAI-Tool`, branch `main`, as one Railway
service. Both the browser workspace and `/api/revector` are served by this service.
Railway reads the committed `railway.json` and builds the committed Dockerfile.
The image includes OpenCV, VTracer, Potrace, resvg, Inkscape and Tesseract.

## Service configuration

1. Create a Railway service from the GitHub repository; use repository root `/`.
2. Attach a persistent Railway volume mounted at `/engine/data` before deploying.
   `requiredMountPath` prevents an accidental deployment without that volume.
3. Set the variables below. Do not override the Docker start command. Railway
   supplies `PORT`; the launcher binds `0.0.0.0` at that port.
4. Deploy, wait for `/health/ready` to pass, then generate a Railway public domain
   for the service. The domain root opens the workspace.

| Variable | Value |
| --- | --- |
| `REVECTOR_DATA_DIR` | `/engine/data` |
| `REVECTOR_WORKER_THREADS` | `2` |
| `REVECTOR_SYNC_JOBS` | `false` |
| `REVECTOR_STORAGE_BACKEND` | `local` |

Use one replica and one API worker with the local queue. The image's launcher
initializes volume ownership, then drops to UID 10001 before starting the API.
Project artifacts and job metadata survive replacement containers on this volume.
Interrupted processing jobs are reported as failed after restart and can be
resubmitted; a local worker does not resume a native trace mid-operation.

`railway.json` defines Docker build, `/health/ready`, a 180-second health-check
timeout, one replica, no overlap, 190-second draining and bounded failure restarts.
No OpenAI key is required for the deterministic workflow. The OpenAI provider is
not implemented/connected in this release; adding an environment key alone does
not activate AI. Authentication/credits remain a JerseyOS integration responsibility.

## Connection indicators

The frontend shows three small status lights, with no setup banner:

- Server: a successful response from the actual ReVector `/health` endpoint.
- Engine: initialization, OpenCV availability, real paths-only SVG validation and
  a successful resvg render in `/health/ready`.
- Tool: the engine checks plus an actual storage write/read, queue initialization,
  workspace availability and Inkscape availability.

All three must pass to display **Ready** and enable uploads. Checks use five-second
request timeouts. Pending lights pulse; failure lights are red. A Retry control
appears only next to a failed segment. Retrying runs the actual checks again.
Browser readiness is separate from each project's True Vector validation: exports
still require validated geometry and zero raster references.

## Verify a deployed service

Replace `YOUR_SERVICE_DOMAIN` with the domain Railway actually generated:

```bash
curl -f https://YOUR_SERVICE_DOMAIN/health
curl -f https://YOUR_SERVICE_DOMAIN/health/ready
REVECTOR_TEST_URL=https://YOUR_SERVICE_DOMAIN node scripts/browser-smoke.cjs
```

The browser smoke script requires Playwright and Chromium as documented in
`WEB_WORKSPACE.md`. It executes real upload, analysis, segmentation, sizing, tracing,
validation, editing and download checks. Never report a Railway URL as deployed
until the deployment is successful and those remote checks have been executed.

## Local container verification

```bash
docker compose up --build
curl -f http://localhost:8000/health/ready
```

The container accepts a custom `PORT` and a freshly mounted volume. Docker Compose
grants only CHOWN, SETUID and SETGID for volume initialization; the API runs non-root.
In a managed environment using a TLS proxy, supply its combined trusted CA bundle
as the optional BuildKit secret `proxy_ca` when building. The CA mount is ephemeral,
and ordinary Railway builds use pip's normal certificate verification.
