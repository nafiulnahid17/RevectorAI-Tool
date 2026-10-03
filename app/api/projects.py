from fastapi import APIRouter, Request, Response, HTTPException
from app.api.schemas import CreateProject, PartUpdate, PartAction, StageRequest, SettingsUpdate, VectorEdit
from app.core.exceptions import EngineError

router = APIRouter(prefix="/api/revector", tags=["projects and manual corrections"])


def public_project(project):
    return {**project.model_dump(mode="json"), "true_vector_ready": project.true_vector_ready}


@router.post("/projects", status_code=201)
def create(body: CreateProject, request: Request):
    return public_project(request.app.state.engine.create(**body.model_dump()))


@router.get("/projects/{project_id}")
def get(project_id: str, request: Request):
    return public_project(request.app.state.engine.load(project_id))


@router.get("/projects/{project_id}/status")
def status(project_id: str, request: Request):
    p = request.app.state.engine.load(project_id)
    return {"project_id": project_id, "status": p.state, "true_vector_ready": p.true_vector_ready,
            "error": p.error, "usage": p.usage}


@router.get("/projects/{project_id}/parts")
def parts(project_id: str, request: Request):
    return request.app.state.engine.load(project_id).parts


@router.get("/projects/{project_id}/validation")
def validation(project_id: str, request: Request):
    return request.app.state.engine.load(project_id).validation


@router.get("/projects/{project_id}/exports")
def exports(project_id: str, request: Request):
    return request.app.state.engine.load(project_id).exports


@router.put("/projects/{project_id}/settings")
def settings(project_id: str, body: SettingsUpdate, request: Request):
    request.app.state.queue.assert_idle(project_id)
    return public_project(request.app.state.engine.update_settings(project_id, body.model_dump(exclude_unset=True)))


@router.post("/segments/{part_id}/confirm")
def confirm(part_id: str, body: StageRequest, request: Request):
    request.app.state.queue.assert_idle(body.project_id)
    return public_project(request.app.state.engine.manual(body.project_id, "confirm", part_id, {}))


@router.post("/segments/{part_id}/update")
def update(part_id: str, body: PartUpdate, request: Request):
    request.app.state.queue.assert_idle(body.project_id)
    changes = body.model_dump(exclude={"project_id"}, exclude_unset=True)
    return public_project(request.app.state.engine.manual(body.project_id, "update", part_id, changes))


@router.post("/parts/actions")
def part_action(body: PartAction, request: Request):
    request.app.state.queue.assert_idle(body.project_id)
    changes = body.model_dump(exclude={"project_id", "action", "part_id"}, exclude_none=True)
    if body.action == "add" and "polygon" not in changes:
        raise EngineError("INVALID_BOUNDARY", "Add requires a polygon")
    return public_project(request.app.state.engine.manual(body.project_id, body.action, body.part_id, changes))


@router.post("/segments/{part_id}/vector-edit")
def vector_edit(part_id: str, body: VectorEdit, request: Request):
    request.app.state.queue.assert_idle(body.project_id)
    return public_project(request.app.state.engine.edit_fill(body.project_id, part_id, body.shape_id, body.fill))


@router.delete("/projects/{project_id}", status_code=204)
def delete(project_id: str, request: Request):
    request.app.state.queue.assert_idle(project_id)
    request.app.state.engine.delete(project_id)
    return Response(status_code=204)


@router.get("/projects/{project_id}/artifacts/{artifact_path:path}")
def artifact(project_id: str, artifact_path: str, request: Request):
    from fastapi.responses import FileResponse
    p = request.app.state.engine.load(project_id)
    key = f"projects/{project_id}/{artifact_path}"
    allowed = {p.source_file, p.working_image, p.corrected_image, p.thumbnail, p.master_svg,
               *p.previews.values(), *p.exports.values()}
    for part in p.parts:
        allowed.update([part.mask, part.source_crop, part.corrected_crop, part.clean_reference, part.vectorization_source, part.vector,
                        request.app.state.engine.key(p, f"vectors/{part.part_id}.svg")])
        allowed.update(part.exports.values())
    allowed.add(request.app.state.engine.key(p, "reports/validation.json"))
    if key not in allowed or not request.app.state.engine.storage.exists(key):
        raise HTTPException(404, "Artifact does not exist in the current project manifest")
    return FileResponse(request.app.state.engine.storage.path(key), filename=artifact_path.rsplit("/", 1)[-1],
                        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox"})
