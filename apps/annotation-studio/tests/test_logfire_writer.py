from contextlib import contextmanager

import logfire
import pytest
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from annotation_studio.logfire_writer import AnnotationWriter, WritebackError


class FakeLogfire:
    def __init__(self):
        self.context = {}
        self.events = []
        self.flush_result = True

    def info(self, message, **attrs):
        self.context = dict(attrs)  # simplification: test reads attach_context's carrier separately
        self.events.append(attrs)

    def force_flush(self, timeout_millis=3000):
        return self.flush_result


def annotation():
    return {"id": 11, "revision": 2, "trace_id": "01a045b8d6d40acd6c98ee00f1a3fe93",
            "span_id": "c7a2373c3fe61d3f", "project_id": 1, "description": "Grounded"}


def annotator():
    return {"id": 7, "name": "Ada"}


def label():
    return {"id": 3, "name": "Pass"}


@pytest.fixture
def fake_logfire():
    return FakeLogfire()


def test_uses_local_configuration(monkeypatch):
    calls = []
    monkeypatch.setattr(logfire, "configure", lambda **kw: calls.append(kw) or FakeLogfire())
    AnnotationWriter("write")
    assert calls == [{"local": True, "token": "write", "service_name": "annotation-studio-writeback"}]


def test_attaches_parent_and_tags_reviewer(fake_logfire):
    AnnotationWriter("write", client=fake_logfire).write(annotation(), annotator(), label())
    assert fake_logfire.events[0]["event_key"] == "annotation:11:revision:2"
    assert "annotator-7" in fake_logfire.events[0]["_tags"]


def test_uses_explicit_traceparent_propagator(monkeypatch, fake_logfire):
    # Regression test: logfire.attach_context()'s *default* propagator can be guard-wrapped
    # depending on distributed_tracing config and silently no-op the extraction, which would
    # make the write-back an orphan log instead of a child of the source span. The writer
    # must pass Logfire's own TraceContextTextMapPropagator() explicitly — the same thing
    # logfire.experimental.annotations.raw_annotate_span does for the same reason.
    calls = []
    real_attach_context = logfire.attach_context

    @contextmanager
    def spy(carrier, **kwargs):
        calls.append((carrier, kwargs.get("propagator")))
        with real_attach_context(carrier, **kwargs):
            yield

    monkeypatch.setattr(logfire, "attach_context", spy)
    AnnotationWriter("write", client=fake_logfire).write(annotation(), annotator(), label())
    carrier, propagator = calls[0]
    assert carrier["traceparent"] == "00-01a045b8d6d40acd6c98ee00f1a3fe93-c7a2373c3fe61d3f-01"
    assert isinstance(propagator, TraceContextTextMapPropagator)


def test_uses_logfire_feedback_attribute_conventions(fake_logfire):
    AnnotationWriter("write", client=fake_logfire).write(annotation(), annotator(), label())
    attrs = fake_logfire.events[0]
    assert attrs["logfire.feedback.name"] == "label"
    assert attrs["logfire.feedback.comment"] == annotation()["description"]


def test_rejects_invalid_trace_or_span_id(fake_logfire):
    with pytest.raises(WritebackError):
        AnnotationWriter("write", client=fake_logfire).write(
            {**annotation(), "trace_id": "not-hex"}, annotator(), label()
        )


def test_false_flush_result_is_a_write_failure(fake_logfire):
    fake_logfire.flush_result = False
    with pytest.raises(WritebackError):
        AnnotationWriter("write", client=fake_logfire).write(annotation(), annotator(), label())
