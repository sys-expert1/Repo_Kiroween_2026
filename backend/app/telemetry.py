"""OpenTelemetry tracing correlated by run_id and stage.

Off by default: with no provider configured, the OpenTelemetry API is a no-op. Set `NECRO_OTEL` to
`console` (print spans to stderr) or `otlp` (needs `opentelemetry-exporter-otlp`; standard
`OTEL_EXPORTER_OTLP_*` variables apply). Span attributes carry identifiers, durations and exit codes
only. Command output and exception text are never attached raw: exception messages are redacted.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

from app.execution.redact import redact

log = logging.getLogger(__name__)
TRACER_NAME = "repo-necromancer"
MAX_MESSAGE = 200


def configure_tracing(mode: str) -> bool:
    """Install a global tracer provider for `mode`. Returns True if tracing is now exporting."""
    mode = mode.strip().lower()
    if mode in ("", "off"):
        return False
    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

        if mode == "console":
            exporter = ConsoleSpanExporter()
        elif mode == "otlp":
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            exporter = OTLPSpanExporter()
        else:
            log.warning("Unknown NECRO_OTEL value %r; tracing stays off.", mode)
            return False
    except ImportError as exc:
        log.warning("Tracing requested but a package is missing (%s); tracing stays off.", exc.name)
        return False
    provider = TracerProvider(resource=Resource.create({"service.name": TRACER_NAME}))
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return True


@contextmanager
def span(
    name: str, run_id: str, stage: str | None = None, **attributes: str | int | float | bool
) -> Iterator[Span]:
    """A span tagged with run and stage. Failures are recorded redacted, then re-raised."""
    attrs: dict[str, str | int | float | bool] = {"necro.run_id": run_id, **attributes}
    if stage:
        attrs["necro.stage"] = stage
    tracer = trace.get_tracer(TRACER_NAME)
    with tracer.start_as_current_span(
        name, attributes=attrs, record_exception=False, set_status_on_exception=False
    ) as current:
        try:
            yield current
        except BaseException as exc:
            message = redact(str(exc))[:MAX_MESSAGE]
            current.add_event(
                "exception", {"exception.type": type(exc).__name__, "exception.message": message}
            )
            current.set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {message}"))
            raise


def record_command(command: str, exit_code: int | None, seconds: float, timed_out: bool) -> None:
    """Attach a finished command to the current span. Only the program name is recorded."""
    program = command.split(" ", 1)[0].rsplit("/", 1)[-1]
    trace.get_current_span().add_event(
        "command",
        {
            "command.program": program,
            "command.exit_code": -1 if exit_code is None else exit_code,
            "command.duration_seconds": seconds,
            "command.timed_out": timed_out,
        },
    )
