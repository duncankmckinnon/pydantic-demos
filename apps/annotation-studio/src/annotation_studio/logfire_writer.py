import logfire
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from annotation_studio.logfire_client import validate_trace_and_span

# Logfire's own logfire.experimental.annotations module uses this exact propagator for the
# same "attach to a span given its trace/span id" case — reused here rather than relying on
# attach_context()'s default global text-map propagator (see the regression test above).
TRACEPARENT_PROPAGATOR = TraceContextTextMapPropagator()


class WritebackError(Exception):
    pass


def build_event_key(annotation: dict) -> str:
    return f"annotation:{annotation['id']}:revision:{annotation['revision']}"


class AnnotationWriter:
    def __init__(self, write_token: str, client=None):
        self.client = client or logfire.configure(
            local=True, token=write_token, service_name="annotation-studio-writeback"
        )

    def write(self, annotation: dict, annotator: dict, label: dict | None) -> None:
        try:
            validate_trace_and_span(annotation["trace_id"], annotation["span_id"])
        except ValueError as exc:
            raise WritebackError(str(exc)) from exc

        traceparent = f"00-{annotation['trace_id']}-{annotation['span_id']}-01"
        label_name = label["name"] if label else None

        with logfire.attach_context({"traceparent": traceparent}, propagator=TRACEPARENT_PROPAGATOR):
            self.client.info(
                "annotation_studio.annotation",
                _tags=["annotation-studio", "human-annotation", f"annotator-{annotator['id']}"],
                event_key=build_event_key(annotation),
                annotation_id=annotation["id"],
                annotation_revision=annotation["revision"],
                annotator_id=annotator["id"],
                annotator_name=annotator["name"],
                label_id=label["id"] if label else None,
                label_name=label_name,
                description=annotation["description"],
                project_id=annotation["project_id"],
                source_trace_id=annotation["trace_id"],
                source_span_id=annotation["span_id"],
                # Attribute names Logfire's own logfire.experimental.annotations.
                # record_feedback() uses, so this renders as feedback in the Logfire UI
                # rather than a plain child log entry. record_feedback() itself can't be
                # called directly — it always writes through the *global* Logfire instance,
                # and this app needs a separately-token-scoped local=True client to keep
                # write-back out of annotation-studio's own Logfire project — so the
                # convention is replicated here against self.client instead.
                **{"logfire.feedback.name": "label", "logfire.feedback.comment": annotation["description"]},
            )
        try:
            flushed = self.client.force_flush(timeout_millis=3000)
        except Exception as exc:
            raise WritebackError(f"Logfire exporter raised during flush: {exc}") from exc
        if not flushed:
            raise WritebackError("Logfire exporter did not flush within 3000ms")
