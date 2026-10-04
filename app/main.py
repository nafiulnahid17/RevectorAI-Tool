"""FastAPI application factory, suitable for isolated tests and JerseyOS embedding."""
from contextlib import asynccontextmanager
import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from app import __version__
from app.core.config import Settings, capabilities
from app.core.engine import Engine
from app.core.exceptions import EngineError
from app.jobs.queue import LocalJobQueue
from app.api import projects, upload, processing, upgrade
from app.core.readiness import readiness
from app.core.security import authenticate
from app.core.upload_guard import UploadGuard


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        from starlette.concurrency import run_in_threadpool
        app.state.engine = Engine(settings)
        app.state.queue = LocalJobQueue(app.state.engine)
        app.state.runtime_ready = True
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        logging.getLogger("revector").info("ReVector startup capabilities=%s", capabilities())
        yield
        app.state.runtime_ready = False
        await run_in_threadpool(app.state.queue.shutdown)

    app = FastAPI(title="ReVector Core Engine", version=__version__, lifespan=lifespan,
                  description="True editable geometry. No raster wrappers. Deploy behind JerseyOS authentication.")

    @app.exception_handler(EngineError)
    async def engine_error(request: Request, error: EngineError):
        from app.errors.service import request_error
        if not error.normalized:
            error.normalized = request_error(request,error.code,error.message,error.recoverable)
        return JSONResponse(status_code=error.status, content={"success": False, "error": error.as_dict()})

    app.add_middleware(UploadGuard, limit=settings.max_upload_bytes + 1024 * 1024)

    @app.middleware("http")
    async def gateway_auth(request: Request, call_next):
        protected = request.url.path.startswith('/api/revector/') or request.url.path in {'/docs', '/redoc', '/openapi.json'}
        # Railway's unauthenticated readiness probe remains available. A gateway
        # presenting credentials must prove they are correct before Ready passes.
        if protected or (request.url.path == '/health/ready' and request.headers.get('authorization')):
            try:
                authenticate(request, settings)
            except EngineError as error:
                from app.errors.normalization import normalize
                error.normalized = normalize(error.code,error.message,error.recoverable,phase='authentication')
                return JSONResponse(status_code=error.status, content={'success': False, 'error': error.as_dict()},
                                    headers={'Cache-Control': 'no-store'})
        response = await call_next(request)
        if protected:
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get("/health", tags=["health"])
    def health():
        return {"status": "ok", "engine": "ReVector", "version": __version__, "dependencies": capabilities(),
                "queue": "local_thread", "ai_required": False}

    @app.get("/health/ready", tags=["health"])
    def ready_health():
        report = readiness(app)
        return JSONResponse(status_code=200 if report['status'] == 'ready' else 503, content=report,
                            headers={"Cache-Control": "no-store"})

    app.include_router(projects.router)
    app.include_router(upload.router)
    app.include_router(processing.router)
    app.include_router(upgrade.router)

    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException
    from app.errors.normalization import normalize
    from app.errors.service import request_error

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # Never return Pydantic's input field: it can contain credentials/oversized data.
        return JSONResponse(status_code=422,content={'success':False,'error':request_error(request,'INVALID_REQUEST','Request fields did not match the API contract',False)})

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        code = exc.detail.get('code','REQUEST_FAILED') if isinstance(exc.detail,dict) else 'REQUEST_FAILED'
        message = exc.detail.get('message','Request failed') if isinstance(exc.detail,dict) else str(exc.detail)
        return JSONResponse(status_code=exc.status_code,content={'success':False,'error':request_error(request,code,message)})

    @app.exception_handler(Exception)
    async def unknown_error(request, exc):
        return JSONResponse(status_code=500,content={'success':False,'error':request_error(request,'UNKNOWN_ERROR','Unexpected server failure; contact the operator with this error id')})
    @app.get('/')
    def root():
        return {'engine': 'ReVector', 'service': 'API', 'website': 'deployed separately on Cloudflare'}
    return app


app = create_app()
