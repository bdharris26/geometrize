"""Small structured error boundary for HTTP clients."""

from __future__ import annotations

from http import HTTPStatus


class APIError(ValueError):
    def __init__(self, code: str, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.code = code
        self.status = status

    def payload(self) -> dict[str, str]:
        return {"error": str(self), "code": self.code}
