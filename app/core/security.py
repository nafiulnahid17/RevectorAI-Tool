"""Authenticate the trusted gateway and isolate projects by external identity."""
import re
import secrets
from fastapi import Request
from app.core.exceptions import EngineError


def configured(settings) -> bool:
    return bool(settings.api_key and len(settings.api_key.get_secret_value()) >= 32)


def authenticate(request: Request, settings) -> None:
    request.state.principal = None
    if settings.allow_unauthenticated:
        return
    if not configured(settings):
        raise EngineError('ENGINE_AUTH_NOT_CONFIGURED', 'Engine authentication is not configured', status=503)
    supplied = request.headers.get('authorization', '')
    expected = 'Bearer ' + settings.api_key.get_secret_value()
    if not secrets.compare_digest(supplied.encode(), expected.encode()):
        raise EngineError('UNAUTHORIZED', 'Engine authentication required', status=401)
    if request.url.path.startswith('/api/revector/'):
        principal = request.headers.get('x-revector-user', '')
        if not re.fullmatch(r'[A-Za-z0-9_:.@-]{1,128}', principal):
            raise EngineError('IDENTITY_REQUIRED', 'A validated external identity is required', status=401)
        request.state.principal = principal


def project_access(request: Request, project_id: str):
    project = request.app.state.engine.load(project_id)
    principal = getattr(request.state, 'principal', None)
    if not request.app.state.engine.settings.allow_unauthenticated and project.user_id != principal:
        # Same result as a nonexistent project; do not reveal another user's data.
        raise EngineError('FILE_NOT_FOUND', 'Project does not exist', status=404)
    return project
