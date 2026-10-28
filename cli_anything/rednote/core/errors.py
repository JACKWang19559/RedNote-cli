class RedNoteError(Exception):
    """An actionable, safe-to-display application error."""

    def __init__(self, code: str, message: str, hint: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def as_dict(self) -> dict:
        result = {"code": self.code, "message": self.message}
        if self.hint:
            result["hint"] = self.hint
        return result
