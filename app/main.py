"""FastAPI application factory, suitable for isolated tests and JerseyOS embedding."""
from contextlib import asynccontextmanager
import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from app import __version__
from app.core.config import Settings, capabilities
from app.core.engine import Engine
from app.core.exceptions import EngineError
from app.jobs.queue import LocalJobQueue
from app.api import projects, upload, processing


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        from starlette.concurrency import run_in_threadpool
        app.state.engine = Engine(settings)
        app.state.queue = LocalJobQueue(app.state.engine)
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        logging.getLogger("revector").info("ReVector startup capabilities=%s", capabilities())
        yield
        await run_in_threadpool(app.state.queue.shutdown)

    app = FastAPI(title="ReVector Core Engine", version=__version__, lifespan=lifespan,
                  description="True editable geometry. No raster wrappers. Deploy behind JerseyOS authentication.")

    @app.exception_handler(EngineError)
    async def engine_error(request: Request, error: EngineError):
        return JSONResponse(status_code=error.status, content={"success": False, "error": error.as_dict()})

    # Bound multipart body before it is spooled. The reverse proxy should enforce the same limit.
    @app.middleware("http")
    async def upload_length(request: Request, call_next):
        if request.url.path == "/api/revector/upload":
            limit = settings.max_upload_bytes + 1024 * 1024
            length = request.headers.get("content-length")
            if length is None:
                return JSONResponse(status_code=411, content={"success": False, "error": {"code": "CONTENT_LENGTH_REQUIRED", "message": "Upload requires Content-Length", "recoverable": True}})
            try:
                if int(length) < 0 or int(length) > limit:
                    raise ValueError
            except ValueError:
                return JSONResponse(status_code=413, content={"success": False, "error": {"code": "UPLOAD_TOO_LARGE", "message": "Multipart upload exceeds configured limit", "recoverable": True}})
        return await call_next(request)

    @app.get("/health", tags=["health"])
    def health():
        return {"status": "ok", "engine": "ReVector", "version": __version__, "dependencies": capabilities(),
                "queue": "local_thread", "ai_required": False}

    app.include_router(projects.router)
    app.include_router(upload.router)
    app.include_router(processing.router)
    web_root = Path(__file__).parent / "web"
    if web_root.exists():
        app.mount("/", StaticFiles(directory=web_root, html=True), name="workspace")
    return app


app = create_app()
