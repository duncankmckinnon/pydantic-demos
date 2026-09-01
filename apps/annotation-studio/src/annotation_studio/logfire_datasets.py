from typing import Any

from logfire.experimental.api_client import AsyncLogfireAPIClient
from pydantic_evals import Case, Dataset

from annotation_studio.logfire_client import fetch_queue_item_content


async def push_queue_dataset(
    read_token: str,
    datasets_token: str,
    name: str,
    annotations: list[dict],
    label_lookup: dict[int, str],
    annotator_lookup: dict[int, str],
) -> dict:
    """Builds one Logfire dataset case per annotation (so an item annotated by two annotators
    produces two cases) and pushes them to Logfire's hosted datasets API. An annotation whose
    source trace/span content can no longer be fetched from Logfire (aged out of the 14-day
    query window, most commonly) is skipped and counted, not treated as a fatal error — the
    caller reports both counts so the export's completeness is visible."""
    pairs = [(a["trace_id"], a["span_id"]) for a in annotations]
    content = await fetch_queue_item_content(read_token, pairs)

    cases: list[Case] = []
    skipped_count = 0
    for annotation in annotations:
        interaction = content.get((annotation["trace_id"], annotation["span_id"]))
        if interaction is None:
            skipped_count += 1
            continue
        if interaction.raw_row is not None:
            inputs: Any = interaction.raw_row
            expected_output: Any = None
        else:
            inputs = interaction.input_text
            expected_output = interaction.output_text or None
        cases.append(
            Case(
                name=f"{annotation['trace_id']}:{annotation['span_id']}:{annotation['annotator_id']}",
                inputs=inputs,
                expected_output=expected_output,
                metadata={
                    "label": label_lookup.get(annotation["label_id"]),
                    "description": annotation["description"],
                    "annotator_name": annotator_lookup.get(annotation["annotator_id"]),
                    "trace_id": annotation["trace_id"],
                    "span_id": annotation["span_id"],
                },
            )
        )

    dataset = Dataset[Any, Any, dict[str, Any]](name=name, cases=cases)
    async with AsyncLogfireAPIClient(api_key=datasets_token) as client:
        await client.push_dataset(dataset)

    return {"name": name, "case_count": len(cases), "skipped_count": skipped_count}
