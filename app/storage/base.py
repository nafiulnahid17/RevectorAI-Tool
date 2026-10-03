from abc import ABC, abstractmethod


class Storage(ABC):
    """Keys are relative object identifiers, never public filesystem paths."""

    @abstractmethod
    def put(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    def get(self, key: str) -> bytes: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def delete_prefix(self, prefix: str) -> None: ...
