class EngineError(Exception):
    """An actionable, public-safe processing failure."""

    def __init__(self, code: str, message: str, recoverable: bool = True, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.recoverable, self.status = code, message, recoverable, status

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "recoverable": self.recoverable}
