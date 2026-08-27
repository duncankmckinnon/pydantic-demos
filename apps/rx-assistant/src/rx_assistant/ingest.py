"""One-time CLI script: embed medicines.csv into the rx-assistant Postgres database.

Run manually (not part of the app's runtime startup, and not part of the default test
suite — it downloads real model weights and needs a real Postgres): the same convention
as apps/chat's evals, run manually via `python -m chat.evals.run`.

    uv run --package rx-assistant python -m rx_assistant.ingest

Safe to re-run: truncates and repopulates both tables every time, so a `medicines.csv`
update just needs a rerun.
"""

import asyncio
import csv
from pathlib import Path

from rx_assistant.db import (
    CREATE_CONDITIONS_INDEX_SQL,
    CREATE_CONDITIONS_TABLE_SQL,
    CREATE_EXTENSION_SQL,
    CREATE_MEDICATIONS_INDEX_SQL,
    CREATE_MEDICATIONS_TABLE_SQL,
    build_medication_embedding_text,
    clean_condition_name,
    create_pool,
)
from rx_assistant.embeddings import encode_texts, load_embedding_model
from rx_assistant.settings import DatabaseSettings

# apps/rx-assistant/src/rx_assistant/ingest.py -> parents[4] is the repo root, same depth
# chat/__init__.py resolves its own .env from (parents[2] there is apps/chat; here we go
# two levels further up past src/rx_assistant/rx-assistant to the repo root).
_DEFAULT_CSV_PATH = Path(__file__).resolve().parents[4] / "medicines.csv"
_BATCH_SIZE = 256


async def _load_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


async def ingest(database_url: str, csv_path: Path = _DEFAULT_CSV_PATH) -> None:
    rows = await _load_rows(csv_path)
    model = load_embedding_model()

    pool = await create_pool(database_url)
    try:
        async with pool.acquire() as conn:
            await conn.execute(CREATE_EXTENSION_SQL)
            await conn.execute(CREATE_CONDITIONS_TABLE_SQL)
            await conn.execute(CREATE_CONDITIONS_INDEX_SQL)
            await conn.execute(CREATE_MEDICATIONS_TABLE_SQL)
            await conn.execute(CREATE_MEDICATIONS_INDEX_SQL)
            await conn.execute("TRUNCATE conditions, medications RESTART IDENTITY CASCADE;")

            condition_names = sorted({clean_condition_name(row["disease_name"]) for row in rows})
            condition_embeddings = encode_texts(model, condition_names)
            condition_id_by_name: dict[str, int] = {}
            for name, embedding in zip(condition_names, condition_embeddings):
                condition_id = await conn.fetchval(
                    "INSERT INTO conditions (name, embedding) VALUES ($1, $2) RETURNING id",
                    name,
                    embedding,
                )
                condition_id_by_name[name] = condition_id

            for batch_start in range(0, len(rows), _BATCH_SIZE):
                batch = rows[batch_start : batch_start + _BATCH_SIZE]
                texts = [
                    build_medication_embedding_text(
                        row["med_name"], row["generic_name"], row["drug_content"]
                    )
                    for row in batch
                ]
                embeddings = encode_texts(model, texts)
                await conn.executemany(
                    "INSERT INTO medications ("
                    "condition_id, med_name, med_url, generic_name, drug_content, "
                    "drug_variant, drug_manufacturer, drug_manufacturer_origin, "
                    "price, final_price, prescription_required, embedding"
                    ") VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)",
                    [
                        (
                            condition_id_by_name[clean_condition_name(row["disease_name"])],
                            row["med_name"],
                            row["med_url"],
                            row["generic_name"],
                            row["drug_content"],
                            row["drug_varient"],
                            row["drug_manufacturer"],
                            row["drug_manufacturer_origin"],
                            row["price"],
                            row["final_price"],
                            row["prescription_required"],
                            embedding,
                        )
                        for row, embedding in zip(batch, embeddings)
                    ],
                )
    finally:
        await pool.close()


if __name__ == "__main__":
    settings = DatabaseSettings()
    asyncio.run(ingest(settings.database_url))
