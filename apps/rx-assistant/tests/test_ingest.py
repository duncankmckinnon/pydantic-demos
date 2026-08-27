from pathlib import Path

from rx_assistant.ingest import _load_rows


async def test_load_rows_reads_csv_into_dicts(tmp_path: Path) -> None:
    csv_path = tmp_path / "drugs.csv"
    csv_path.write_text(
        "medical_condition,drug_name,generic_name,drug_classes,side_effects\n"
        "ADHD,Vyvanse,lisdexamfetamine,CNS stimulants,insomnia\n",
        encoding="utf-8",
    )

    rows = await _load_rows(csv_path)

    assert rows == [
        {
            "medical_condition": "ADHD",
            "drug_name": "Vyvanse",
            "generic_name": "lisdexamfetamine",
            "drug_classes": "CNS stimulants",
            "side_effects": "insomnia",
        }
    ]
