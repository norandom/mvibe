"""Opt-in observability export: OpenObserve-style logs plus optional OTLP telemetry.

Everything here is inert unless explicitly enabled. Credentials come from the
environment only and never appear in emitted records; records are allowlisted
by construction, not redacted. All export failures are counted and swallowed so
the request path is never blocked, and no trace IDs are ever invented.
"""
from __future__ import annotations

import asyncio
import base64
from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from typing import Any

import httpx
from pydantic import SecretStr

SERVICE_NAME = 'mvibe-proxy'

# The only fields that may ever leave the process. Anything else is dropped.
_ALLOWED_FIELDS = frozenset({
    'event', 'timestamp', 'request_id', 'trace_id', 'span_id', 'phase',
    'outcome', 'status', 'elapsed_ms', 'model', 'hint',
})


class ObserveConfigError(ValueError):
    """MVIBE_OBSERVE_* is set but incomplete; export must stay disabled."""


@dataclass(frozen=True, slots=True)
class ObserveConfig:
    """OpenObserve-style ingest configuration, read from the environment only."""

    url: str
    org: str
    stream: str
    user: str
    token: SecretStr

    @classmethod
    def from_env(cls) -> ObserveConfig | None:
        url = os.environ.get('MVIBE_OBSERVE_URL', '').strip().rstrip('/')
        if not url:
            return None
        user = os.environ.get('MVIBE_OBSERVE_USER', '')
        token = os.environ.get('MVIBE_OBSERVE_TOKEN', '')
        if not user or not token:
            raise ObserveConfigError(
                'MVIBE_OBSERVE_URL requires MVIBE_OBSERVE_USER and MVIBE_OBSERVE_TOKEN; '
                'observability export stays disabled')
        return cls(url=url, org=os.environ.get('MVIBE_OBSERVE_ORG') or 'default',
                   stream=os.environ.get('MVIBE_OBSERVE_STREAM') or 'mvibe',
                   user=user, token=SecretStr(token))

    @property
    def logs_url(self) -> str:
        """Native OpenObserve logs ingest: /api/{org}/{stream}/_json."""
        return f'{self.url}/api/{self.org}/{self.stream}/_json'

    @property
    def authorization(self) -> str:
        raw = f'{self.user}:{self.token.get_secret_value()}'.encode()
        return 'Basic ' + base64.b64encode(raw).decode('ascii')


def allowlisted(record: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only schema fields that are present and non-null."""
    return {'service': SERVICE_NAME,
            **{key: record[key] for key in _ALLOWED_FIELDS
               if key in record and record[key] is not None}}


class OpenObserveExporter:
    """Bounded-queue, batched POST of allowlisted records to OpenObserve `_json`.

    `enqueue` is O(1) and never awaited on the request path; a full queue drops
    the oldest record and counts it. A background task batches and posts; every
    failure is counted, never raised. `shutdown` flushes with a hard bound.
    """

    def __init__(self, config: ObserveConfig, *, client: httpx.AsyncClient | None = None,
                 max_queue: int = 1000, batch_size: int = 100, flush_interval: float = 1.0,
                 request_timeout: float = 5.0, shutdown_timeout: float = 2.0) -> None:
        self.config = config
        self._client = client if client is not None else httpx.AsyncClient(timeout=request_timeout)
        self._owns_client = client is None
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max_queue)
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._shutdown_timeout = shutdown_timeout
        self.dropped_events = 0
        self.export_failures = 0
        self.exported_events = 0
        self._task: asyncio.Task[None] | None = None

    def enqueue(self, record: Mapping[str, Any]) -> None:
        item = allowlisted(record)
        try:
            self._queue.put_nowait(item)
            return
        except asyncio.QueueFull:
            pass
        try:
            self._queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        self.dropped_events += 1
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            self.dropped_events += 1

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        while True:
            try:
                first = await asyncio.wait_for(self._queue.get(), timeout=self._flush_interval)
            except asyncio.TimeoutError:
                continue
            batch = [first]
            while len(batch) < self._batch_size:
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            await self._post(batch)

    async def _post(self, batch: list[dict[str, Any]]) -> None:
        try:
            response = await self._client.post(
                self.config.logs_url, content=json.dumps(batch).encode(),
                headers={'Authorization': self.config.authorization,
                         'Content-Type': 'application/json'})
            if response.status_code >= 400:
                self.export_failures += 1
            else:
                self.exported_events += len(batch)
        except Exception:
            self.export_failures += 1

    async def flush(self) -> None:
        batch: list[dict[str, Any]] = []
        while True:
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if batch:
            await self._post(batch)

    async def shutdown(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        try:
            await asyncio.wait_for(self.flush(), timeout=self._shutdown_timeout)
        except Exception:
            self.export_failures += 1
        if self._owns_client:
            await self._client.aclose()


class Telemetry:
    """Handle for the optional OTel providers; shutdown is bounded and safe."""

    def __init__(self, tracer_provider, meter_provider, tracer, meter) -> None:
        self.tracer_provider = tracer_provider
        self.meter_provider = meter_provider
        self.tracer = tracer
        self.requests = meter.create_counter('mvibe.proxy.requests')
        self.stream_outcomes = meter.create_counter('mvibe.proxy.stream_outcomes')
        self.export_failures = meter.create_counter('mvibe.proxy.export_failures')
        self.dropped_events = meter.create_counter('mvibe.proxy.dropped_events')

    def shutdown(self) -> None:
        try:
            self.tracer_provider.shutdown()
        except Exception:
            pass
        try:
            self.meter_provider.shutdown()
        except Exception:
            pass


def start_telemetry(service_version: str = '0.1.0', *, span_exporter=None,
                    metric_reader=None) -> Telemetry | None:
    """Start OTLP telemetry, or return None when the optional extra is absent.

    Production exporters are configured purely by standard OTEL_EXPORTER_OTLP_*
    env vars (for OpenObserve: endpoint `{url}/api/{org}`, headers
    `Authorization=Basic …` and `stream-name=mvibe`). Tests may inject an
    in-memory span exporter and metric reader instead.
    """
    try:
        from opentelemetry import metrics, trace
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        return None
    resource = Resource.create({'service.name': SERVICE_NAME, 'service.version': service_version})
    if span_exporter is None:
        span_exporter = OTLPSpanExporter()
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
    trace.set_tracer_provider(tracer_provider)
    if metric_reader is None:
        metric_reader = PeriodicExportingMetricReader(OTLPMetricExporter())
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)
    meter = metrics.get_meter('mvibe.proxy')
    return Telemetry(tracer_provider, meter_provider, trace.get_tracer('mvibe.proxy'), meter)


def current_trace_ids() -> tuple[str, str] | None:
    """(trace_id, span_id) of the active span when telemetry is on; else None."""
    try:
        from opentelemetry import trace
    except ImportError:
        return None
    context = trace.get_current_span().get_span_context()
    if context is None or not context.is_valid:
        return None
    return f'{context.trace_id:032x}', f'{context.span_id:016x}'


def inject_traceparent(headers: dict[str, str]) -> None:
    """W3C trace context on the approved proxy-to-upstream hop only."""
    try:
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
    except ImportError:
        return
    carrier: dict[str, str] = {}
    TraceContextTextMapPropagator().inject(carrier)
    for key, value in carrier.items():
        if key.lower() == 'traceparent':
            headers[key] = value
