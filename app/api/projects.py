from fastapi import APIRouter, Request, Response, HTTPException
from app.api.schemas import CreateProject, PartUpdate, PartAction, StageRequest, SettingsUpdate, VectorEdit
from app.core.exceptions import EngineError
from app.core.security import project_access

router = APIRouter(prefix="/api/revector", tags=["projects and manual corrections"])


def public_project(project):
    result = project.model_dump(mode="json")
    result['previews'] = {k:v for k,v in result['previews'].items() if k not in {'vector_view','nodes'}}
    result.pop('assistant_sessions',None)
    result['exports'] = {
        k: v
        for k, v in result['exports'].items()
        if k.startswith(("selected_zip_", "selected_files_", "production_pack_"))
    }
    result['true_vector_ready'] = project.true_vector_ready
    result['ai_capabilities'] = {'native_ai_export':False}
    return result


@router.post("/projects", status_code=201)
def create(body: CreateProject, request: Request):
    fields = body.model_dump()
    if request.state.principal:
        if fields.get('user_id') and fields['user_id'] != request.state.principal:
            raise EngineError('IDENTITY_MISMATCH', 'Project identity must match the authenticated gateway', status=403)
        fields['user_id'] = request.state.principal
    return public_project(request.app.state.engine.create(**fields))


@router.get("/projects/{project_id}")
def get(project_id: str, request: Request):
    return public_project(project_access(request, project_id))


@router.get("/projects/{project_id}/status")
def status(project_id: str, request: Request):
    p = project_access(request, project_id)
    return {"project_id": project_id, "status": p.state, "true_vector_ready": p.true_vector_ready,
            "error": p.error, "usage": p.usage}


@router.get("/projects/{project_id}/parts")
def parts(project_id: str, request: Request):
    return project_access(request, project_id).parts


@router.get("/projects/{project_id}/validation")
def validation(project_id: str, request: Request):
    return project_access(request, project_id).validation


@router.get("/projects/{project_id}/exports")
def exports(project_id: str, request: Request):
    return project_access(request, project_id).exports


@router.put("/projects/{project_id}/settings")
def settings(project_id: str, body: SettingsUpdate, request: Request):
    project_access(request, project_id)
    request.app.state.queue.assert_idle(project_id)
    return public_project(request.app.state.engine.update_settings(project_id, body.model_dump(exclude_unset=True)))


@router.post("/segments/{part_id}/confirm")
def confirm(part_id: str, body: StageRequest, request: Request):
    project_access(request, body.project_id)
    request.app.state.queue.assert_idle(body.project_id)
    return public_project(request.app.state.engine.manual(body.project_id, "confirm", part_id, {}))


@router.post("/segments/{part_id}/update")
def update(part_id: str, body: PartUpdate, request: Request):
    project_access(request, body.project_id)
    request.app.state.queue.assert_idle(body.project_id)
    changes = body.model_dump(exclude={"project_id"}, exclude_unset=True)
    return public_project(request.app.state.engine.manual(body.project_id, "update", part_id, changes))


@router.post("/parts/actions")
def part_action(body: PartAction, request: Request):
    project_access(request, body.project_id)
    request.app.state.queue.assert_idle(body.project_id)
    changes = body.model_dump(exclude={"project_id", "action", "part_id"}, exclude_none=True)
    if body.action == "add" and "polygon" not in changes:
        raise EngineError("INVALID_BOUNDARY", "Add requires a polygon")
    return public_project(request.app.state.engine.manual(body.project_id, body.action, body.part_id, changes))


@router.post("/segments/{part_id}/vector-edit")
def vector_edit(part_id: str, body: VectorEdit, request: Request):
    project_access(request, body.project_id)
    request.app.state.queue.assert_idle(body.project_id)
    return public_project(request.app.state.engine.edit_fill(body.project_id, part_id, body.shape_id, body.fill))


@router.delete("/projects/{project_id}", status_code=204)
def delete(project_id: str, request: Request):
    project_access(request, project_id)
    request.app.state.queue.assert_idle(project_id)
    request.app.state.engine.delete(project_id)
    return Response(status_code=204)


@router.get("/projects/{project_id}/artifacts/{artifact_path:path}")
def artifact(project_id: str, artifact_path: str, request: Request):
    from fastapi.responses import FileResponse
    p = project_access(request, project_id)
    key = f"projects/{project_id}/{artifact_path}"
    allowed = {p.source_file, p.working_image, p.corrected_image, p.thumbnail,
               *[v for k,v in p.previews.items() if k not in {'vector_view','nodes'}], *p.exports.values(), *p.ai_assets.values()}
    for part in p.parts:
        allowed.update([part.mask, part.source_crop, part.corrected_crop, part.clean_reference, part.vectorization_source, part.vector,
                        request.app.state.engine.key(p, f"vectors/{part.part_id}.svg")])
        allowed.update(part.exports.values())
        allowed.update(part.previews.values())
    allowed.add(request.app.state.engine.key(p, "reports/validation.json"))
    export_artifact = (
        artifact_path.startswith("exports/")
        and request.app.state.engine.storage.exists(key)
    )
    if (key not in allowed and not export_artifact) or not request.app.state.engine.storage.exists(key):
        raise HTTPException(404, "Artifact does not exist in the current project manifest")
    return FileResponse(request.app.state.engine.storage.path(key), filename=artifact_path.rsplit("/", 1)[-1],
                        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox"})
