"""Atomic, traversal-safe filesystem storage with inter-process project locks."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID
from app.core.exceptions import EngineError
from app.models.project import Project, now
from app.storage.base import Storage


class LocalStorage(Storage):
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / ".locks").mkdir(exist_ok=True)

    def path(self, key: str) -> Path:
        if not key or "\\" in key or Path(key).is_absolute() or ".." in Path(key).parts:
            raise EngineError("INVALID_STORAGE_KEY", "Invalid storage key", status=400)
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise EngineError("INVALID_STORAGE_KEY", "Storage key leaves configured root", status=400)
        return path

    def put(self, key: str, data: bytes) -> None:
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            temp = stream.name
        os.replace(temp, path)

    def get(self, key: str) -> bytes:
        try:
            return self.path(key).read_bytes()
        except FileNotFoundError as exc:
            raise EngineError("FILE_NOT_FOUND", "Requested artifact does not exist", status=404) from exc

    def exists(self, key: str) -> bool:
        return self.path(key).is_file()

    def delete_prefix(self, prefix: str) -> None:
        import shutil
        target = self.path(prefix)
        if target == self.root:
            raise EngineError("INVALID_STORAGE_KEY", "Cannot delete storage root")
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()

    @staticmethod
    def project_key(project_id: str) -> str:
        try:
            project_id = str(UUID(project_id))
        except ValueError as exc:
            raise EngineError("INVALID_PROJECT_ID", "Expected a UUID project id", status=400) from exc
        return f"projects/{project_id}/project.json"

    def load(self, project_id: str) -> Project:
        return Project.model_validate_json(self.get(self.project_key(project_id)))

    def save(self, project: Project) -> None:
        project.updated_at = now()
        self.put(self.project_key(project.project_id), project.model_dump_json(indent=2).encode())

    @contextmanager
    def lock(self, project_id: str):
        self.project_key(project_id)
        with (self.root / ".locks" / project_id).open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise EngineError("PROJECT_BUSY", "Another operation is running on this project", status=409) from exc
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def json(self, key: str, value) -> None:
        self.put(key, json.dumps(value, indent=2).encode())
