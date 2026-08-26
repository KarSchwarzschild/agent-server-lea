"""
OpenTelemetry 集成 — trace/span/metrics。

- 每次 LLM 调用、工具执行自动生成 trace span
- 自定义指标：P50/P99 延迟、token 用量、错误率
- 开发环境输出到控制台，生产环境接 Jaeger/OTLP
"""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager, contextmanager

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

tracer = trace.get_tracer("lea-platform")
meter = metrics.get_meter("lea-platform")

llm_request_duration = meter.create_histogram(
    name="llm.request.duration",
    description="LLM request duration in seconds",
    unit="s",
)

llm_token_counter = meter.create_counter(
    name="llm.tokens.total",
    description="Total tokens consumed",
)

llm_error_counter = meter.create_counter(
    name="llm.errors.total",
    description="Total LLM call errors",
)

tool_request_duration = meter.create_histogram(
    name="tool.request.duration",
    description="Tool execution duration in seconds",
    unit="s",
)


def setup_tracing(service_name: str = "lea-platform") -> None:
    """初始化 OpenTelemetry TracerProvider + MeterProvider。"""
    resource = Resource.create({"service.name": service_name})

    # Tracing
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

    # Metrics
    if otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

            reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=otlp_endpoint))
        except ImportError:
            reader = PeriodicExportingMetricReader(ConsoleMetricExporter())
    else:
        reader = PeriodicExportingMetricReader(ConsoleMetricExporter(), export_interval_millis=60000)
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))


@asynccontextmanager
async def trace_llm_call(model: str, purpose: str, tenant_id: str = ""):
    """包装 LLM 调用 — span + 延迟指标 + 错误计数。"""
    start = time.perf_counter()
    attrs = {"llm.model": model, "llm.purpose": purpose, "tenant_id": tenant_id}

    with tracer.start_as_current_span(f"llm.{purpose}", attributes=attrs) as span:
        try:
            yield span
            duration = time.perf_counter() - start
            span.set_attribute("llm.duration_s", round(duration, 3))
            llm_request_duration.record(duration, attributes=attrs)
        except Exception as e:
            duration = time.perf_counter() - start
            span.set_attribute("error", True)
            span.set_attribute("error.message", str(e))
            llm_error_counter.add(1, attributes=attrs)
            llm_request_duration.record(duration, attributes=attrs)
            raise


@asynccontextmanager
async def trace_tool_call(tool_name: str, tenant_id: str = ""):
    """包装工具调用 — span + 延迟指标。"""
    start = time.perf_counter()
    attrs = {"tool.name": tool_name, "tenant_id": tenant_id}

    with tracer.start_as_current_span(f"tool.{tool_name}", attributes=attrs) as span:
        try:
            yield span
            duration = time.perf_counter() - start
            tool_request_duration.record(duration, attributes=attrs)
        except Exception as e:
            span.set_attribute("error", True)
            span.set_attribute("error.message", str(e))
            raise


def record_tokens(model: str, prompt_tokens: int, completion_tokens: int, tenant_id: str = "") -> None:
    """记录 token 消耗。"""
    attrs = {"llm.model": model, "tenant_id": tenant_id}
    llm_token_counter.add(prompt_tokens, attributes={**attrs, "token.type": "prompt"})
    llm_token_counter.add(completion_tokens, attributes={**attrs, "token.type": "completion"})


@contextmanager
def trace_span(name: str, **attributes):
    """通用 span 包装。"""
    with tracer.start_as_current_span(name, attributes=attributes) as span:
        yield span
