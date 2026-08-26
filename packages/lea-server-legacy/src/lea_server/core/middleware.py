"""
请求级中间件 — 租户注入、请求追踪。

TenantMiddleware 从 JWT 的 tenant_id 字段或 X-Tenant-Id header 解析租户，
注入 ContextVar，请求结束时自动清理。

注意：用纯 ASGI 实现（而非 Starlette BaseHTTPMiddleware），因为后者会
缓冲 StreamingResponse body，把 SSE 长连接吃成秒断。参考：
https://github.com/encode/starlette/issues/1012
"""

from __future__ import annotations

import time
import uuid

import structlog

from lea_server.core.auth import decode_token
from lea_server.core.tenant import (
    DEFAULT_TENANT_ID,
    TenantContext,
    clear_tenant,
    set_tenant,
)

logger = structlog.get_logger()


def _get_header(scope, key: bytes) -> str:
    """从 ASGI scope["headers"] 里取 header（小写 bytes key）。"""
    for name, value in scope.get("headers", ()):
        if name == key:
            try:
                return value.decode("latin-1")
            except Exception:
                return ""
    return ""


class TenantMiddleware:
    """从请求解析 tenant_id 并注入 ContextVar。纯 ASGI 版本，不缓冲响应流。

    解析优先级：
    1. JWT payload 中的 tenant_id 字段
    2. X-Tenant-Id 请求头
    3. 默认 tenant
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        tenant_id = self._extract_tenant_id(scope)
        set_tenant(TenantContext(tenant_id=tenant_id))
        try:
            await self.app(scope, receive, send)
        finally:
            clear_tenant()

    @staticmethod
    def _extract_tenant_id(scope) -> str:
        auth = _get_header(scope, b"authorization")
        if auth.startswith("Bearer "):
            token_data = decode_token(auth[7:])
            if token_data and getattr(token_data, "tenant_id", None):
                return token_data.tenant_id
        header_tenant = _get_header(scope, b"x-tenant-id")
        if header_tenant:
            return header_tenant
        return DEFAULT_TENANT_ID


class RequestTraceMiddleware:
    """请求追踪 — 生成 request_id，记录访问日志。纯 ASGI 实现（不吞 SSE 流）。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming_id = _get_header(scope, b"x-request-id")
        request_id = incoming_id or uuid.uuid4().hex[:8]
        start = time.perf_counter()

        structlog.contextvars.bind_contextvars(request_id=request_id)
        status_holder = {"status": 500}

        request_id_bytes = request_id.encode("latin-1")

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message.get("status", 500)
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id_bytes))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
            logger.exception(
                "request_failed",
                method=scope.get("method"),
                path=scope.get("path"),
                elapsed_ms=elapsed_ms,
            )
            raise
        else:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
            logger.info(
                "request_completed",
                method=scope.get("method"),
                path=scope.get("path"),
                status=status_holder["status"],
                elapsed_ms=elapsed_ms,
            )
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
