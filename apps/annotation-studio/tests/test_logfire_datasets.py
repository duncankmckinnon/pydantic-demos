from annotation_studio import logfire_datasets
from annotation_studio.logfire_client import Interaction


class FakeContentFetcher:
    def __init__(self, content: dict):
        self.content = content
        self.calls = []

    async def __call__(self, read_token, items):
        self.calls.append((read_token, items))
        return self.content


class FakeDatasetsClient:
    def __init__(self):
        self.pushed = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def push_dataset(self, dataset, **kwargs):
        self.pushed.append(dataset)
        return {"name": dataset.name}


def _annotation(trace_id="01a045b8d6d40acd6c98ee00f1a3fe93", span_id="c7a2373c3fe61d3f", annotator_id=1, label_id=10, description="why") -> dict:
    return {
        "trace_id": trace_id, "span_id": span_id, "annotator_id": annotator_id,
        "label_id": label_id, "description": description,
    }


async def test_push_queue_dataset_builds_one_case_per_annotation(monkeypatch) -> None:
    trace_id = "01a045b8d6d40acd6c98ee00f1a3fe93"
    fetcher = FakeContentFetcher({
        (trace_id, "c7a2373c3fe61d3f"): Interaction(
            trace_id=trace_id, span_id="c7a2373c3fe61d3f", start_timestamp="2026-08-28T00:00:00Z",
            input_text="q", output_text="a", full_conversation=[], trace_url="https://example.test",
        ),
    })
    monkeypatch.setattr(logfire_datasets, "fetch_queue_item_content", fetcher)
    fake_client = FakeDatasetsClient()
    monkeypatch.setattr(logfire_datasets, "AsyncLogfireAPIClient", lambda api_key: fake_client)

    result = await logfire_datasets.push_queue_dataset(
        "read-token", "datasets-token", "my-dataset", [_annotation()],
        label_lookup={10: "Pass"}, annotator_lookup={1: "Ada"},
    )

    assert result == {"name": "my-dataset", "case_count": 1, "skipped_count": 0}
    pushed_dataset = fake_client.pushed[0]
    assert pushed_dataset.name == "my-dataset"
    case = pushed_dataset.cases[0]
    # Logfire's hosted datasets API requires inputs/expected_output to be JSON objects, not
    # bare strings — it rejects a raw string with a 422 (confirmed against the real API).
    assert case.inputs == {"text": "q"}
    assert case.expected_output == {"text": "a"}
    assert case.metadata == {
        "label": "Pass", "description": "why", "annotator_name": "Ada",
        "trace_id": trace_id, "span_id": "c7a2373c3fe61d3f",
    }


async def test_push_queue_dataset_one_case_per_annotator_for_the_same_item(monkeypatch) -> None:
    trace_id = "01a045b8d6d40acd6c98ee00f1a3fe93"
    fetcher = FakeContentFetcher({
        (trace_id, "c7a2373c3fe61d3f"): Interaction(
            trace_id=trace_id, span_id="c7a2373c3fe61d3f", start_timestamp="2026-08-28T00:00:00Z",
            input_text="q", output_text="a", full_conversation=[], trace_url="https://example.test",
        ),
    })
    monkeypatch.setattr(logfire_datasets, "fetch_queue_item_content", fetcher)
    fake_client = FakeDatasetsClient()
    monkeypatch.setattr(logfire_datasets, "AsyncLogfireAPIClient", lambda api_key: fake_client)
    annotations = [
        _annotation(annotator_id=1, label_id=10),
        _annotation(annotator_id=2, label_id=20),
    ]

    result = await logfire_datasets.push_queue_dataset(
        "read-token", "datasets-token", "my-dataset", annotations,
        label_lookup={10: "Pass", 20: "Fail"}, annotator_lookup={1: "Ada", 2: "Grace"},
    )

    assert result["case_count"] == 2


async def test_push_queue_dataset_skips_items_whose_trace_aged_out(monkeypatch) -> None:
    fetcher = FakeContentFetcher({})
    monkeypatch.setattr(logfire_datasets, "fetch_queue_item_content", fetcher)
    fake_client = FakeDatasetsClient()
    monkeypatch.setattr(logfire_datasets, "AsyncLogfireAPIClient", lambda api_key: fake_client)

    result = await logfire_datasets.push_queue_dataset(
        "read-token", "datasets-token", "my-dataset", [_annotation()],
        label_lookup={10: "Pass"}, annotator_lookup={1: "Ada"},
    )

    assert result == {"name": "my-dataset", "case_count": 0, "skipped_count": 1}


async def test_push_queue_dataset_uses_raw_row_when_structured_parse_unavailable(monkeypatch) -> None:
    trace_id = "01a045b8d6d40acd6c98ee00f1a3fe93"
    fetcher = FakeContentFetcher({
        (trace_id, "c7a2373c3fe61d3f"): Interaction(
            trace_id=trace_id, span_id="c7a2373c3fe61d3f", start_timestamp="2026-08-28T00:00:00Z",
            input_text="", output_text="", full_conversation=[], trace_url="https://example.test",
            raw_row={"score": 0.9},
        ),
    })
    monkeypatch.setattr(logfire_datasets, "fetch_queue_item_content", fetcher)
    fake_client = FakeDatasetsClient()
    monkeypatch.setattr(logfire_datasets, "AsyncLogfireAPIClient", lambda api_key: fake_client)

    result = await logfire_datasets.push_queue_dataset(
        "read-token", "datasets-token", "my-dataset", [_annotation()],
        label_lookup={10: "Pass"}, annotator_lookup={1: "Ada"},
    )

    assert result["case_count"] == 1
    case = fake_client.pushed[0].cases[0]
    assert case.inputs == {"score": 0.9}
    assert case.expected_output is None
