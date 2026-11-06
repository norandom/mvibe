"""Opt-in proxy stdout events. No shared logging configuration or payload inspection.

Structlog's processor pipeline permits future OpenTelemetry context enrichment;
no trace IDs are invented here and no exporter is installed.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
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


# The econ event schema (design: Data Models). The per-request event carries
# the counters EconShunt.apply records plus an outcome; the warm event carries
# the outcome, latency and size. Only these names may ever leave the process.
_ECON_EVENT_KEYS = (
    'econ_outcome', 'econ_eligible', 'econ_replaced', 'econ_bypassed',
    'econ_pending', 'econ_in_bytes', 'econ_out_bytes', 'econ_est_saved_tokens',
)
_ECON_WARM_KEYS = ('econ_outcome', 'econ_latency_ms', 'econ_in_bytes')


def _econ_fields(econ) -> dict:
    """The allowlisted econ_* view of one request's economics (Req 5.1, 5.2).

    EconShunt.apply records its EconCounters object on the diagnostic (the
    seam its tests assert); the proxy's error path records a plain mapping.
    Either way only allowlisted names survive — ints and the outcome enum,
    never content, hints or paths (Req 1.4). The estimate keeps its 'est'
    labelling and may be negative; it is relayed unchanged.
    """
    if isinstance(econ, Mapping):
        return {key: econ[key] for key in _ECON_EVENT_KEYS
                if key in econ and econ[key] is not None}
    return {
        'econ_outcome': 'applied',
        'econ_eligible': econ.eligible,
        'econ_replaced': econ.replaced,
        'econ_bypassed': econ.bypassed,
        'econ_pending': econ.pending,
        'econ_in_bytes': econ.in_bytes,
        'econ_out_bytes': econ.out_bytes,
        'econ_est_saved_tokens': econ.est_saved_tokens,
    }


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
        # Econ counters (EconCounters) or an allowlisted econ_* mapping; set
        # only when economics acted on this request — absent otherwise.
        self.econ = None

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
            econ = _econ_fields(self.econ) if self.econ is not None else {}
            if _logger is not None:
                _logger.info('proxy_request', request_id=self.request_id,
                             phase=self.phase, outcome=self.outcome, status=self.status,
                             elapsed_ms=round((time.monotonic() - self.started) * 1000, 2),
                             model=self.model, hint=self.hint, **econ)
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
                    **econ,
                })
        except Exception:
            # A closed stdout, faulty processor or failing exporter must never break inference.
            pass


def emit_econ_warm(record: Mapping) -> None:
    """Emit one allowlisted econ_warm event (Req 5.4) through the same
    stdout and export channels as the per-request event.

    The warm queue's event carries exactly the outcome, latency and size;
    anything else in `record` is dropped by construction, and a closed
    stdout or failing exporter must never break a warm fetch.
    """
    try:
        fields = {key: record[key] for key in _ECON_WARM_KEYS
                  if key in record and record[key] is not None}
        if _logger is not None:
            _logger.info('econ_warm', **fields)
        if _exporter is not None:
            _exporter.enqueue({'event': 'econ_warm',
                               'timestamp': datetime.now(timezone.utc).isoformat(),
                               **fields})
    except Exception:
        # Same never-raise discipline as RequestDiagnostic.emit.
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
