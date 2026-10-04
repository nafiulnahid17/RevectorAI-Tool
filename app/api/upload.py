from fastapi import APIRouter, File, Form, Request, UploadFile
from app.api.projects import public_project
from app.core.exceptions import EngineError
from app.core.security import project_access

router = APIRouter(prefix="/api/revector", tags=["upload"])


@router.post("/upload")
def upload(request: Request, project_id: str = Form(...), file: UploadFile = File(...), auto_prepare: bool = Form(False)):
    project_access(request, project_id)
    request.app.state.queue.assert_idle(project_id)
    limit = request.app.state.engine.settings.max_upload_bytes
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise EngineError("UPLOAD_TOO_LARGE", "Upload exceeds configured limit", status=413)
    p=request.app.state.engine.upload(project_id, data, file.filename or "upload", file.content_type)
    if auto_prepare:
        job=request.app.state.queue.submit(project_id,'prepare',{})
        return {**public_project(request.app.state.engine.load(project_id)),'preparation_job':job}
    return public_project(p)
