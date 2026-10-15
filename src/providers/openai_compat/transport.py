"""传输：只做「发一次 HTTP、读回响应体」。抽出来是为了能在测试里换掉它。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from kernel.model import (
    TransientError,
)
from providers.openai_compat.payload import classify_openai_error

# ---- 传输 ----------------------------------------------------------------------


@dataclass(frozen=True)
class HttpRequest:
    """一次 HTTP 请求的描述——传输层据此去发。"""

    url: str
    body: dict[str, Any]
    headers: dict[str, str]


class Transport:
    """HTTP 传输契约：可注入，所以整条路都能离线验证。"""

    def post_json(self, request: HttpRequest, *, timeout: float) -> dict[str, Any]:
        """发一次请求，返回解析后的 JSON。"""
        raise NotImplementedError

    def post_sse(self, request: HttpRequest, *, timeout: float) -> Iterator[str]:
        """发一次请求，返回**行迭代器**（SSE）。"""
        raise NotImplementedError


class UrllibTransport(Transport):
    """标准库实现。不引任何依赖是刻意的（见模块说明）。"""

    def _open(self, request: HttpRequest, *, timeout: float):
        payload = json.dumps(request.body, ensure_ascii=False).encode("utf-8")
        prepared = urllib.request.Request(
            request.url,
            data=payload,
            headers={"Content-Type": "application/json", **request.headers},
            method="POST",
        )
        try:
            return urllib.request.urlopen(prepared, timeout=timeout)
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:  # pragma: no cover - 读不到就算了
                body = str(exc)
            raise classify_openai_error(exc.code, body) from exc
        except urllib.error.URLError as exc:
            raise TransientError(f"连接失败：{exc}") from exc

    def post_json(self, request: HttpRequest, *, timeout: float) -> dict[str, Any]:
        with self._open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def post_sse(self, request: HttpRequest, *, timeout: float) -> Iterator[str]:
        response = self._open(request, timeout=timeout)

        def lines() -> Iterator[str]:
            with response:
                for raw in response:
                    yield raw.decode("utf-8", errors="replace")

        return lines()
