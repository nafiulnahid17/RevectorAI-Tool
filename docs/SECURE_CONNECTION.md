# Cloudflare website to engine connection

`Browser → same-origin Cloudflare Worker → HTTPS + bearer key → ReVector API`

The website is in `nafiulnahid17/RevectorAi-WEB`; this repository is the engine.
The Worker stores `ENGINE_API_KEY` as a Cloudflare encrypted secret. Railway stores
the identical value as `REVECTOR_API_KEY`. Values must be random and at least 32
characters. The website build does not read engine secrets, and the browser never
receives them. Changing the key requires updating both services.

The Worker also holds an independent `SESSION_SIGNING_KEY`. It validates/mints an
HttpOnly, Secure, SameSite=Lax signed session cookie, strips browser-supplied
authorization/identity headers, and forwards only its validated `anon_...` identity
in `X-Revector-User`. The engine owns authorization: another identity cannot read,
edit, delete, download, inspect or cancel a project's jobs or artifacts.

Project creation assigns the authenticated owner; conflicting `user_id` fields are
rejected. Existing ownerless projects created before authentication remain on disk
but are not automatically exposed to anonymous sessions. An administrator must
explicitly assign ownership during a host-application migration.

Protected API, schema and documentation routes fail closed without credentials.
Public `/health` permits deployment liveness checks. Public `/health/ready` checks
configured authentication, storage, queue, vector validation/rendering and conversion
availability. A readiness request presenting credentials must authenticate them,
so an incorrectly configured Worker cannot report a valid engine connection.

No wildcard CORS is enabled. Worker mutations require their actual website Origin;
cross-site fetches are rejected. Uploads stream through the gateway, and the engine
bounds multipart bytes even without Content-Length. JSON commands are size-bounded
at the Worker. Downloads remain protected and marked no-store; SVG sandbox policy
is preserved. The Worker refuses redirects that could forward the engine key to
another host, and the upstream address is server-configured rather than supplied
by browsers.

This release isolates anonymous sessions. A cookie remains valid for seven days
of inactivity; clearing or losing it prevents access to its existing projects.
It is not account login, billing or abuse prevention. Add JerseyOS identity or
Cloudflare Access and request/credit limits for a restricted commercial service.
Keep authentication secrets out of repository files and public build variables.
