"""OpenTelemetry 集成 — trace/span/metrics。

开发环境：输出到控制台
生产环境：通过 OTEL_EXPORTER_OTLP_ENDPOINT 发送到收集器
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

tracer = trace.get_tracer("lea-platform")


def setup_tracing(service_name: str = "lea-platform") -> None:
    """初始化 OpenTelemetry TracerProvider。"""
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint)))
        except ImportError:
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    else:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)


@asynccontextmanager
async def trace_llm_call(model: str, purpose: str):
    """包装LLM调用，自动记录span。"""
    with tracer.start_as_current_span(f"llm.{purpose}") as span:
        span.set_attribute("llm.model", model)
        span.set_attribute("llm.purpose", purpose)
        yield span


@asynccontextmanager
async def trace_tool_call(tool_name: str):
    """包装工具调用，自动记录span。"""
    with tracer.start_as_current_span(f"tool.{tool_name}") as span:
        span.set_attribute("tool.name", tool_name)
        yield span
