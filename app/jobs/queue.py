"""Bounded thread queue for one API process; jobs are persisted for polling and audit."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from uuid import UUID, uuid4
from app.core.engine import Engine
from app.core.exceptions import EngineError
from app.models.project import now


class LocalJobQueue:
    def __init__(self, engine: Engine, max_pending: int = 32):
        self.engine = engine
        self.pool = ThreadPoolExecutor(max_workers=engine.settings.worker_threads, thread_name_prefix="revector")
        self.guard = Lock()
        self.events: dict[str, Event] = {}
        self.active: dict[str, str] = {}
        self.max_pending = max_pending
        # A local queue cannot resume a job whose worker died. Report it honestly.
        for path in (engine.storage.root / "jobs").glob("*.json"):
            import json
            job = json.loads(path.read_bytes())
            if job["status"] in {"queued", "processing", "cancelling"}:
                job.update(status="failed", finished_at=now(), error={"code": "WORKER_RESTARTED", "message": "Local worker restarted; resubmit job", "recoverable": True})
                self.write(job)
                from app.errors.service import record
                try:
                    record(engine,job['error'])
                except Exception:
                    from app.core.logging import log_event
                    log_event('interrupted_job_record_unavailable',job_id=job['job_id'])

    def write(self, job: dict):
        job['job_state'] = {'queued':'QUEUED','processing':'RUNNING','completed':'SUCCEEDED','failed':'FAILED','cancelled':'CANCELLED'}.get(job['status'],'RUNNING')
        job['updated_at'] = now()
        if job.get('error') and not job['error'].get('error_id'):
            from app.errors.normalization import normalize
            raw=job['error']
            job['error']=normalize(raw.get('code','JOB_ERROR'),raw.get('message','Job failed'),raw.get('recoverable',True),phase=job['stage'],project_id=job['project_id'],job_id=job['job_id'],part_id=job.get('part_id'))
        job.setdefault('started_at',None)
        job.setdefault('completed_at',None)
        job.setdefault('part_id',None)
        job.setdefault('error',None)
        if job['status'] in {'completed','failed','cancelled'}:
            job['completed_at'] = job.get('finished_at') or now()
        self.engine.storage.json(f"jobs/{job['job_id']}.json", job)

    def get(self, job_id: str) -> dict:
        try:
            job_id = str(UUID(job_id))
        except ValueError as exc:
            raise EngineError("INVALID_JOB_ID", "Expected a UUID job id", status=400) from exc
        import json
        job = json.loads(self.engine.storage.get(f"jobs/{job_id}.json"))
        if job['status'] == 'processing':
            p = self.engine.load(job['project_id'])
            job['process_event'] = p.events[-1] if p.events else None
            if job['process_event'] and job['process_event'].get('part_id'):
                job['part_id']=job['process_event']['part_id']
        return job

    def assert_idle(self, project_id: str):
        with self.guard:
            if project_id in self.active:
                raise EngineError("PROJECT_BUSY", "A job is queued or processing for this project", status=409)

    def submit(self, project_id: str, stage: str, params: dict | None = None) -> dict:
        self.engine.load(project_id)
        with self.guard:
            if project_id in self.active:
                raise EngineError("PROJECT_BUSY", "A job is already queued or running for this project", status=409)
            if len(self.active) >= self.max_pending:
                raise EngineError("QUEUE_FULL", "Processing queue is full; retry later", status=503)
            job_id = str(uuid4())
            job = {"job_id": job_id, "project_id": project_id, "stage": stage, "status": "queued", "created_at": now()}
            event = Event()
            self.events[job_id] = event
            self.active[project_id] = job_id
            job['part_id'] = (params or {}).get('part_id')
            self.write(job)
        if self.engine.settings.sync_jobs:
            self.execute(job, params or {}, event)
            return self.get(job_id)
        self.pool.submit(self.execute, job, params or {}, event)
        return job

    def execute(self, job: dict, params: dict, event: Event):
        job.update(status="processing", started_at=now())
        self.write(job)
        import time
        started=time.monotonic()
        def checkpoint():
            if time.monotonic()-started > self.engine.settings.job_timeout_seconds:
                raise EngineError('JOB_TIMEOUT','Job exceeded its configured time budget at a safe processing boundary')
            return event.is_set()
        try:
            result = self.engine.run(job["project_id"], job["stage"], params, job_id=job["job_id"], cancelled=checkpoint)
            if result.get("export_errors"):
                job.update(status="failed", result=result, error={"code": "EXPORT_CONVERSION_FAILED", "message": "Some requested formats failed", "recoverable": True})
            else:
                job.update(status="completed", result=result)
        except EngineError as exc:
            from app.errors.normalization import normalize
            job.update(status="cancelled" if exc.code == "JOB_CANCELLED" else "failed", error=exc.normalized or normalize(exc.code,exc.message,exc.recoverable,phase=job['stage'],project_id=job['project_id'],job_id=job['job_id'],part_id=job.get('part_id')))
        except Exception:
            job.update(status="failed", error={"code": "WORKER_FAILED", "message": "Unexpected worker failure", "recoverable": True})
        finally:
            job["finished_at"] = now()
            self.write(job)
            if job.get('error'):
                from app.errors.service import record
                try:
                    record(self.engine,job['error'])
                except Exception:
                    from app.core.logging import log_event
                    log_event('error_record_unavailable',job_id=job['job_id'])
            with self.guard:
                self.active.pop(job["project_id"], None)
                self.events.pop(job["job_id"], None)

    def cancel(self, job_id: str) -> dict:
        job = self.get(job_id)
        with self.guard:
            event = self.events.get(job_id)
            if event:
                event.set()
        return {"job_id": job_id, "cancellation_requested": event is not None,
                "note": "Cancellation is cooperative at part/stage boundaries; native traces finish before stopping."}

    def shutdown(self):
        self.pool.shutdown(wait=True, cancel_futures=False)
