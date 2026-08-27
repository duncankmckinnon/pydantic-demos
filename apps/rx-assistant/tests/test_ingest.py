from pathlib import Path

from rx_assistant.ingest import _load_rows


async def test_load_rows_reads_csv_into_dicts(tmp_path: Path) -> None:
    csv_path = tmp_path / "medicines.csv"
    csv_path.write_text(
        "disease_name,med_name,generic_name,drug_content\n"
        "ADHD (1),Atrest 25mg,Tetrabenazine,Some content\n",
        encoding="utf-8",
    )

    rows = await _load_rows(csv_path)

    assert rows == [
        {
            "disease_name": "ADHD (1)",
            "med_name": "Atrest 25mg",
            "generic_name": "Tetrabenazine",
            "drug_content": "Some content",
        }
    ]
