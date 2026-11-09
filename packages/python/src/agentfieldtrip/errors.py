"""Exceptions raised by the Agent Field Trip SDK."""

from __future__ import annotations

from typing import Any, Optional


class AgentFieldTripError(Exception):
    """Raised when the API answers with an error status or the network fails.

    Attributes:
        status: HTTP status code, or ``None`` for network failures (no response).
        code: machine-readable code, taken from the ``error`` field of the body or,
            for moderation rejections, from ``motivo`` (for example ``contains_link``).
            ``"network_error"`` when no response was received.
        detail: the human-readable ``detalle`` field of the body (English), if any.
        retry_after: seconds from the ``Retry-After`` header, if it was a number.
        body: the parsed JSON body (or the raw text when it was not JSON).
    """

    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        code: Optional[str] = None,
        detail: Optional[str] = None,
        retry_after: Optional[float] = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.detail = detail
        self.retry_after = retry_after
        self.body = body

    def __repr__(self) -> str:
        return (
            f"AgentFieldTripError(status={self.status!r}, code={self.code!r}, "
            f"detail={self.detail!r}, retry_after={self.retry_after!r})"
        )


class InvalidArgumentError(AgentFieldTripError, ValueError):
    """Raised locally, before any request, when an argument is invalid.

    ``code`` is ``name_invalid`` (visitor name), ``invalid_id`` (an id that does not match the
    API's pattern) or ``base_url_invalid``. It is also a ``ValueError``; ``status`` is ``None``.
    """

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message, code=code, detail=message)
