"""
请求级中间件 — 租户注入、请求追踪。

TenantMiddleware 从 JWT 的 tenant_id 字段或 X-Tenant-Id header 解析租户，
注入 ContextVar，请求结束时自动清理。
"""

from __future__ import annotations

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from lea_server.core.auth import decode_token
from lea_server.core.tenant import (
    DEFAULT_TENANT_ID,
    TenantContext,
    clear_tenant,
    set_tenant,
)

logger = structlog.get_logger()


class TenantMiddleware(BaseHTTPMiddleware):
    """从请求中解析 tenant_id 并注入 ContextVar。

    解析优先级：
    1. JWT payload 中的 tenant_id 字段
    2. X-Tenant-Id 请求头
    3. 默认 tenant
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        tenant_id = self._extract_tenant_id(request)
        ctx = TenantContext(tenant_id=tenant_id)
        set_tenant(ctx)
        try:
            response = await call_next(request)
            return response
        finally:
            clear_tenant()

    def _extract_tenant_id(self, request: Request) -> str:
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            token_data = decode_token(token)
            if token_data and hasattr(token_data, "tenant_id"):
                return token_data.tenant_id or DEFAULT_TENANT_ID

        header_tenant = request.headers.get("x-tenant-id", "")
        if header_tenant:
            return header_tenant

        return DEFAULT_TENANT_ID


class RequestTraceMiddleware(BaseHTTPMiddleware):
    """请求追踪 — 生成 request_id，记录请求日志。"""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id", str(uuid.uuid4())[:8])
        start = time.perf_counter()

        structlog.contextvars.bind_contextvars(request_id=request_id)

        try:
            response = await call_next(request)
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "request_completed",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                elapsed_ms=round(elapsed_ms, 1),
            )
            response.headers["X-Request-Id"] = request_id
            return response
        except Exception:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.exception(
                "request_failed",
                method=request.method,
                path=request.url.path,
                elapsed_ms=round(elapsed_ms, 1),
            )
            raise
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
