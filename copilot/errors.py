"""Stable, user-facing failures; raw provider bodies and credentials stay private."""


class CopilotError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def as_dict(self) -> dict:
        return {"outcome": "error", "code": self.code, "message": self.message}


class Refusal(CopilotError):
    """An understood request that this product will not approximate."""


class InvalidExtraction(ValueError):
    """Untrusted model output failed structural or evidence validation."""

    def __init__(self, message: str, *, retain_context: bool = False):
        super().__init__(message)
        self.retain_context = retain_context
