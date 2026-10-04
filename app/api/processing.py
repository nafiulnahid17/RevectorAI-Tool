from fastapi import APIRouter, Request
from app.core.security import project_access
from app.models.upgrade import JobResponse
from app.api.schemas import StageRequest, GeometryRequest, SegmentRequest, ExportRequest

router = APIRouter(prefix="/api/revector", tags=["processing jobs"])


def submit(stage, body, request):
    project_access(request, body.project_id)
    return request.app.state.queue.submit(body.project_id, stage, body.model_dump(exclude={"project_id"}, exclude_none=True))


@router.post("/correct-geometry", status_code=202,response_model=JobResponse)
def geometry(body: GeometryRequest, request: Request):
    return submit("correct-geometry", body, request)


@router.post("/segment", status_code=202,response_model=JobResponse)
def segment(body: SegmentRequest, request: Request):
    return submit("segment", body, request)


@router.post("/export", status_code=202,response_model=JobResponse)
def export(body: ExportRequest, request: Request):
    return submit("export", body, request)


def stage_handler(stage):
    def handler(body: StageRequest, request: Request):
        return submit(stage, body, request)
    handler.__name__ = stage
    return handler


for stage in ["analyze", "reconstruct", "vectorize", "optimize", "compose", "validate"]:
    router.add_api_route("/" + stage, stage_handler(stage), methods=["POST"], status_code=202,response_model=JobResponse)


@router.get("/jobs/{job_id}",response_model=JobResponse)
def job(job_id: str, request: Request):
    result = request.app.state.queue.get(job_id)
    project_access(request, result["project_id"])
    return result


@router.post("/jobs/{job_id}/cancel", status_code=202)
def cancel(job_id: str, request: Request):
    project_access(request, request.app.state.queue.get(job_id)["project_id"])
    return request.app.state.queue.cancel(job_id)
