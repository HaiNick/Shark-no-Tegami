"""Error type that send_email turns into a structured error result."""


class TegamiError(Exception):
    def __init__(self, code: str, message: str, details: list[str] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []

    def to_result(self) -> dict:
        out = {"status": "error", "code": self.code, "message": self.message}
        if self.details:
            out["details"] = self.details
        return out
