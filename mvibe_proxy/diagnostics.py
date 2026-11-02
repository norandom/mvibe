"""Opt-in proxy stdout events. No shared logging configuration or payload inspection.

Structlog's processor pipeline permits future OpenTelemetry context enrichment;
no trace IDs are invented here and no exporter is installed.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import sys
import time
import uuid

import structlog

_logger = None
_exporter = None


def configure_stdout() -> None:
    """Called only by the explicit proxy entry point (or an embedding operator)."""
    global _logger
    _logger = structlog.wrap_logger(
        structlog.PrintLogger(file=sys.stdout),
        processors=[structlog.processors.JSONRenderer()],
    )


def disable_stdout() -> None:
    global _logger
    _logger = None


def configure_exporter(exporter) -> None:
    """Attach the opt-in export sink; called only when export is enabled."""
    global _exporter
    _exporter = exporter


def clear_exporter() -> None:
    global _exporter
    _exporter = None


class RequestDiagnostic:
    def __init__(self):
        self.request_id = uuid.uuid4().hex
        self.started = time.monotonic()
        self.phase = 'request'
        self.outcome = 'completed'
        self.status = None
        self.hint = 'Request completed; the agent executes tools from returned tool_calls.'
        # Optional correlation fields; populated only when telemetry is active.
        self.trace_id = None
        self.span_id = None
        self.model = None

    def failure(self, code: str) -> None:
        # Only fixed codes chosen by owned call sites reach the log renderer.
        self.phase, self.outcome, self.hint = {
            'auth': ('authentication', 'auth_failed', 'Run proxy.py --login to renew Vibe OIDC authentication.'),
            'connection': ('acquisition', 'connection_failed', 'Check Vibe connectivity and proxy configuration; no inference was replayed.'),
            'unsupported': ('routing', 'unsupported_endpoint', 'Use models, chat/completions or embeddings; Responses is unsupported.'),
            'invalid': ('routing', 'invalid_request', 'The request body does not match the endpoint dialect; check the adapter schema in /openapi.json.'),
            'interrupted': ('stream', 'stream_interrupted', 'Stream interrupted; inspect agent state before any explicit retry.'),
            'cancelled': ('stream', 'cancelled', 'Request cancelled; inspect agent state before any explicit retry.'),
            'cleanup': ('cleanup', 'cleanup_failed', 'Upstream cleanup failed; restart the proxy before sending another request.'),
        }[code]

    def upstream(self, status: int) -> None:
        if status >= 400:
            self.phase, self.outcome = 'upstream', 'upstream_error'
            self.hint = {
                400: 'Check tool_schema and request fields against the selected upstream model capabilities.',
                401: 'Run proxy.py --login; verify Vibe OIDC access.',
                403: 'Check Vibe tenant and model access; the agent owns tool permissions.',
                422: 'Check tool schema and reasoning history serialization against the selected model; update the provider before an explicit retry.',
                429: 'Respect upstream rate_limit guidance before an explicit retry.',
            }.get(status, 'Check upstream availability before an explicit retry; no inference was replayed.')

    def emit(self) -> None:
        try:
            if _logger is not None:
                _logger.info('proxy_request', request_id=self.request_id,
                             phase=self.phase, outcome=self.outcome, status=self.status,
                             elapsed_ms=round((time.monotonic() - self.started) * 1000, 2),
                             model=self.model, hint=self.hint)
            if _exporter is not None:
                # The exporter allowlists by construction; None fields are dropped.
                _exporter.enqueue({
                    'event': 'proxy_request',
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'request_id': self.request_id,
                    'trace_id': self.trace_id,
                    'span_id': self.span_id,
                    'phase': self.phase,
                    'outcome': self.outcome,
                    'status': self.status,
                    'elapsed_ms': round((time.monotonic() - self.started) * 1000, 2),
                    'model': self.model,
                    'hint': self.hint,
                })
        except Exception:
            # A closed stdout, faulty processor or failing exporter must never break inference.
            pass


class ProxyDiagnostics:
    """Pure ASGI wrapper: observe headers and lifetime without reading any body."""
    def __init__(self, app):
        self.app = app

    async def _call(self, scope, receive, send, diagnostic):
        try:
            await self.app(scope, receive, send)
        except asyncio.CancelledError:
            if diagnostic.outcome != 'cleanup_failed':
                diagnostic.failure('cancelled')
            raise
        except Exception:
            if diagnostic.outcome != 'cleanup_failed':
                diagnostic.failure('interrupted')
            raise
        finally:
            diagnostic.emit()

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        diagnostic = RequestDiagnostic()
        scope['mvibe.diagnostic'] = diagnostic

        async def observe(message):
            if message['type'] == 'http.response.start':
                diagnostic.status = message['status']
                if diagnostic.status in (404, 405) and diagnostic.outcome == 'completed':
                    diagnostic.failure('unsupported')
                message = dict(message)
                message['headers'] = list(message.get('headers', [])) + [
                    (b'x-request-id', diagnostic.request_id.encode('ascii'))]
            await send(message)

        app = scope.get('app')
        telemetry = getattr(app.state, 'telemetry', None) if app is not None else None
        if telemetry is not None:
            from mvibe_proxy.observability import current_trace_ids
            with telemetry.tracer.start_as_current_span('proxy.request'):
                ids = current_trace_ids()
                if ids is not None:
                    diagnostic.trace_id, diagnostic.span_id = ids
                await self._call(scope, receive, observe, diagnostic)
        else:
            await self._call(scope, receive, observe, diagnostic)
