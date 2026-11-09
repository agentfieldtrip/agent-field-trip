"""Test helpers: a scripted fake transport and a recording sleep."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

Body = Union[bytes, Iterable[bytes]]


def jbody(obj: Any) -> bytes:
    return json.dumps(obj).encode("utf-8")


class Call:
    def __init__(
        self, method: str, url: str, headers: Dict[str, str], body: Optional[bytes], stream: bool
    ) -> None:
        self.method = method
        self.url = url
        self.headers = headers
        self.body = body
        self.stream = stream

    @property
    def json(self) -> Any:
        assert self.body is not None
        return json.loads(self.body.decode("utf-8"))


class FakeTransport:
    """Returns scripted responses in order; the last one repeats. An Exception is raised."""

    def __init__(self, *responses: Any) -> None:
        self.responses: List[Any] = list(responses) or [(200, {}, jbody({}))]
        self.calls: List[Call] = []

    def __call__(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        body: Optional[bytes],
        timeout: float,
        stream: bool,
    ) -> Tuple[int, Dict[str, str], Body]:
        self.calls.append(Call(method, url, dict(headers), body, stream))
        idx = min(len(self.calls) - 1, len(self.responses) - 1)
        r = self.responses[idx]
        if isinstance(r, Exception):
            raise r
        return r  # type: ignore[no-any-return]


class Sleeper:
    def __init__(self) -> None:
        self.slept: List[float] = []

    def __call__(self, seconds: float) -> None:
        self.slept.append(seconds)


def ok(obj: Any, status: int = 200, **headers: str) -> Tuple[int, Dict[str, str], bytes]:
    return status, {k.lower().replace("_", "-"): v for k, v in headers.items()}, jbody(obj)
