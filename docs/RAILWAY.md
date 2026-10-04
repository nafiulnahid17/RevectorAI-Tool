# Railway engine deployment

Deploy `nafiulnahid17/RevectorAI-Tool`, branch `main`, as a Docker service.
This repository serves the engine API only. Deploy the website separately from
[RevectorAi-WEB](https://github.com/nafiulnahid17/RevectorAi-WEB) to Cloudflare Workers.

## Configuration

1. Connect the engine repository, using repository root `/`.
2. Attach a persistent volume mounted at `/engine/data` before deploying.
3. Set the variables below. Generate a random API key of at least 32 characters
   (for example, `openssl rand -hex 32`) and keep it in Railway's secret variables.
4. Keep the Docker start command: `PORT` is read automatically.
5. Deploy, verify `/health/ready`, then generate a Railway HTTPS public domain.

| Variable | Value |
| --- | --- |
| `REVECTOR_API_KEY` | Private random gateway key |
| `REVECTOR_ALLOW_UNAUTHENTICATED` | `false` |
| `REVECTOR_DATA_DIR` | `/engine/data` |
| `REVECTOR_WORKER_THREADS` | `2` |
| `REVECTOR_SYNC_JOBS` | `false` |
| `REVECTOR_STORAGE_BACKEND` | `local` |

Set the website's `ENGINE_ORIGIN` to that HTTPS origin (without a path). Store the
identical API key as its Cloudflare `ENGINE_API_KEY` secret. Give the website an
independent random `SESSION_SIGNING_KEY` secret. No keys belong in browser code.
See [secure connection](SECURE_CONNECTION.md) for identity and authorization.

Use one replica and one API process with the local queue. The launcher initializes
volume ownership then drops to UID 10001. Artifacts and manifests persist across
replacement containers. Interrupted processing jobs become failed after restart;
resubmit them explicitly. Jobs do not resume a native trace mid-operation.

`railway.json` configures Docker build, `/health/ready`, a 180-second health timeout,
one replica, required volume, no overlap and bounded failure restarts.
The image includes Inkscape, Potrace, Tesseract and Python CV/vector dependencies.
OpenAI is not connected in this release; adding a key does not activate AI.

## Readiness and verification

Public `/health` checks liveness. `/health/ready` checks engine initialization,
OpenCV, actual paths-only SVG validation/rendering, storage write/read, queue,
Inkscape and authentication configuration. Missing authentication fails readiness.
A request presenting an incorrect bearer key is rejected even on readiness.
API, job and artifact routes require authentication and project ownership.

```bash
curl -f https://YOUR_ENGINE_DOMAIN/health
curl -f https://YOUR_ENGINE_DOMAIN/health/ready
# In a trusted operator environment only:
curl -f https://YOUR_ENGINE_DOMAIN/api/revector/projects/PROJECT_UUID \
  -H "Authorization: Bearer $ENGINE_API_KEY" \
  -H "X-Revector-User: OWNER_IDENTITY"
```

The Cloudflare UI shows Server, Engine and Tool lights. Server checks the actual
Worker; Engine and Tool check the authenticated upstream readiness response.
Ready requires all three, with retry controls only on failed segments.
Run the browser smoke script from the website repository against its deployed URL
before claiming the hosted workflow works.

## Local container

Copy `.env.example` to `.env` and set `REVECTOR_API_KEY` before running:

```bash
docker compose up --build
curl -f http://localhost:8000/health/ready
```

Compose grants only CHOWN, SETUID and SETGID for volume initialization; the API runs
non-root. Railway uses the plain root Dockerfile with normal certificate
verification, without custom secret mounts or an external Dockerfile frontend.

If your local development environment routes HTTPS through a managed TLS proxy,
use the helper with that environment's combined CA bundle:

```bash
python scripts/build_local_image.py --proxy-ca /etc/ssl/certs/ca-certificates.crt \
  --tag revector-engine:local
```

The helper generates a temporary Dockerfile, mounts the CA only for pip, keeps TLS
verification enabled and leaves the production Dockerfile unchanged. Do not add
local proxy CA mounts to Railway's production Dockerfile.

## If a deployment fails

Railway's Metal Dockerfile validator supports only `type=cache` RUN mounts.
A production `--mount=type=secret,id=proxy_ca,...` fails before build execution
with “other mount types are not supported”. The production Dockerfile therefore
uses plain RUN instructions; the CA helper is only for local managed environments.

A failure in **Build image / Dockerfile validation** happens before API startup.
Open **View logs** and inspect the first error; runtime API keys and health checks
cannot repair a Dockerfile validation failure. Ensure Source root is `/`, builder
is Dockerfile, and any `RAILWAY_DOCKERFILE_PATH` override points to `Dockerfile`.
Deploy the latest GitHub commit and use its own logs, not a previous failed build.

If the image builds but **Deploy / Healthcheck** fails, confirm the persistent
volume mount `/engine/data`, `REVECTOR_API_KEY` (32+ characters) and
`REVECTOR_ALLOW_UNAUTHENTICATED=false`. Do not enter the API key in screenshots or
paste it in build logs. Keep the start-command override empty: the packaged
launcher handles the Railway port and mounted-volume permissions.

After deployment passes, go to **Settings → Networking → Generate Domain**.
An unexposed service has no public HTTPS address for the Cloudflare gateway.
