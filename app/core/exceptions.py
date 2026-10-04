class EngineError(Exception):
    """An actionable, public-safe processing failure."""

    def __init__(self, code: str, message: str, recoverable: bool = True, status: int = 422, *, diagnostics: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.recoverable, self.status = code, message, recoverable, status
        self.diagnostics = diagnostics or {}
        self.normalized = None

    def as_dict(self) -> dict:
        if self.normalized is None:
            from app.errors.normalization import normalize
            self.normalized=normalize(self.code,self.message,self.recoverable)
        return self.normalized
