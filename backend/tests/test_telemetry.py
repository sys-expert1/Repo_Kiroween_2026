from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from opentelemetry.trace import StatusCode

from app import telemetry
from app.telemetry import configure_tracing, record_command, span
from tests.test_workflow import CountingAdapter, client_with, run


@pytest.fixture
def spans(span_exporter):
    span_exporter.clear()
    return _Ours(span_exporter)


class _Ours:
    """Only this app's spans: other libraries may emit spans into the same global provider."""

    def __init__(self, exporter) -> None:
        self._exporter = exporter

    def get_finished_spans(self):
        return [s for s in self._exporter.get_finished_spans() if s.name.startswith("necro.")]


def test_a_full_run_produces_spans_tagged_with_run_and_stage(client: TestClient, spans):
    r = run(client, {"fixture_id": "legacy-python-cli"}, "PREVIEW")
    finished = spans.get_finished_spans()
    names = {s.name: s for s in finished}
    for node in (
        "validate_and_snapshot",
        "autopsy",
        "characterization",
        "roadmap",
        "generate_patches",
    ):
        assert f"necro.node.{node}" in names
    assert all(s.attributes["necro.run_id"] == r["id"] for s in finished)
    assert names["necro.node.autopsy"].attributes["necro.stage"] == "AUTOPSY"
    assert names["necro.node.characterization"].attributes["necro.stage"] == "CHARACTERIZATION"
    assert names["necro.node.generate_patches"].attributes["necro.stage"] == "REANIMATION"
    assert "necro.stage" not in names["necro.node.complete_preview"].attributes


def test_commands_are_recorded_as_events_with_program_and_exit_code_only(client: TestClient, spans):
    run(client, {"fixture_id": "legacy-python-cli"}, "INSPECT")
    events = [e for s in spans.get_finished_spans() for e in s.events if e.name == "command"]
    assert events and all(
        set(e.attributes)
        == {
            "command.program",
            "command.exit_code",
            "command.duration_seconds",
            "command.timed_out",
        }
        for e in events
    )
    programs = {e.attributes["command.program"] for e in events}
    assert "python" in programs or any("python" in p for p in programs)
    assert not any("/" in e.attributes["command.program"] for e in events)  # no paths


def test_a_failing_node_marks_the_span_as_an_error_with_redacted_text(settings, spans):
    def boom(_adapter):
        raise RuntimeError("login failed password=hunter2 for https://bob:s3cret@host/x")

    client = client_with(settings, CountingAdapter(boom))
    r = run(client, {"fixture_id": "legacy-python-cli"})
    assert r["status"] == "FAILED"
    [failed] = [s for s in spans.get_finished_spans() if s.name == "necro.node.autopsy"]
    assert failed.status.status_code is StatusCode.ERROR
    blob = repr(failed.status.description) + repr([dict(e.attributes) for e in failed.events])
    assert "RuntimeError" in blob and "hunter2" not in blob and "s3cret" not in blob


def test_spans_never_carry_command_output(client: TestClient, spans):
    run(client, {"fixture_id": "legacy-python-cli"}, "INSPECT")
    for s in spans.get_finished_spans():
        values = list(s.attributes.values()) + [v for e in s.events for v in e.attributes.values()]
        assert not any(isinstance(v, str) and ("FAILED " in v or "passed" in v) for v in values)


def test_span_helper_reraises_and_records(spans):
    with pytest.raises(ValueError):
        with span("necro.test", "run-x", "AUTOPSY", extra=3):
            raise ValueError("token=abc123secret")
    [s] = [s for s in spans.get_finished_spans() if s.name == "necro.test"]
    assert s.attributes["extra"] == 3 and s.status.status_code is StatusCode.ERROR
    assert "abc123secret" not in repr(s.status.description)


def test_record_command_handles_a_missing_exit_code(spans):
    with span("necro.cmd", "run-y"):
        record_command("/usr/bin/pytest -q", None, 1.5, True)
    [s] = [s for s in spans.get_finished_spans() if s.name == "necro.cmd"]
    attrs = dict(s.events[0].attributes)
    assert attrs["command.program"] == "pytest" and attrs["command.exit_code"] == -1
    assert attrs["command.timed_out"] is True


def test_tracing_is_off_unless_asked_for(caplog):
    assert configure_tracing("") is False and configure_tracing("off") is False
    with caplog.at_level(logging.WARNING):
        assert configure_tracing("carrier-pigeon") is False
    assert "stays off" in caplog.text


def test_otlp_without_its_package_stays_off_with_a_warning(monkeypatch, caplog):
    import sys

    monkeypatch.setitem(sys.modules, "opentelemetry.exporter.otlp.proto.http.trace_exporter", None)
    with caplog.at_level(logging.WARNING):
        assert configure_tracing("otlp") is False
    assert "package is missing" in caplog.text


def test_console_mode_installs_a_provider(monkeypatch):
    installed = []
    monkeypatch.setattr(telemetry.trace, "set_tracer_provider", installed.append)
    assert configure_tracing("console") is True and len(installed) == 1
