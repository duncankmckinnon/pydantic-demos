import re
from dataclasses import dataclass

import asyncpg

CREATE_EXTENSION_SQL = "CREATE EXTENSION IF NOT EXISTS vector;"

CREATE_CONDITIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS conditions (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    embedding vector(384) NOT NULL
);
"""

CREATE_CONDITIONS_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS conditions_embedding_idx "
    "ON conditions USING hnsw (embedding vector_cosine_ops);"
)

CREATE_MEDICATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS medications (
    id SERIAL PRIMARY KEY,
    condition_id INTEGER NOT NULL REFERENCES conditions(id),
    med_name TEXT NOT NULL,
    med_url TEXT,
    generic_name TEXT,
    drug_content TEXT,
    drug_variant TEXT,
    drug_manufacturer TEXT,
    drug_manufacturer_origin TEXT,
    price TEXT,
    final_price TEXT,
    prescription_required TEXT,
    embedding vector(384) NOT NULL
);
"""

CREATE_MEDICATIONS_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS medications_embedding_idx "
    "ON medications USING hnsw (embedding vector_cosine_ops);"
)

_DISEASE_COUNT_SUFFIX_RE = re.compile(r"\s*\(\d+\)\s*$")


def clean_condition_name(disease_name: str) -> str:
    """Strip the trailing scrape-artifact count, e.g. "ADHD (7)" -> "ADHD"."""
    return _DISEASE_COUNT_SUFFIX_RE.sub("", disease_name).strip()


def build_medication_embedding_text(
    med_name: str, generic_name: str | None, drug_content: str | None
) -> str:
    """Combine the fields worth matching a symptom/condition/drug-name query against."""
    parts = [med_name, generic_name, drug_content]
    return " ".join(part.strip() for part in parts if part and part.strip())


@dataclass
class ConditionMatch:
    name: str
    distance: float


@dataclass
class MedicationMatch:
    med_name: str
    generic_name: str | None
    drug_content: str | None
    drug_manufacturer: str | None
    price: str | None
    prescription_required: str | None
    condition_name: str
    distance: float


async def create_pool(database_url: str) -> asyncpg.Pool:
    """Open an asyncpg pool with the pgvector codec registered on every connection, so
    Python lists round-trip as Postgres `vector` values."""
    from pgvector.asyncpg import register_vector

    return await asyncpg.create_pool(database_url, init=register_vector)


_CONDITIONS_SQL = (
    "SELECT name, embedding <=> $1 AS distance "
    "FROM conditions ORDER BY embedding <=> $1 LIMIT $2"
)

_MEDICATIONS_SQL = (
    "SELECT m.med_name, m.generic_name, m.drug_content, m.drug_manufacturer, "
    "m.price, m.prescription_required, c.name AS condition_name, "
    "m.embedding <=> $1 AS distance "
    "FROM medications m JOIN conditions c ON c.id = m.condition_id "
)

_MEDICATIONS_UNFILTERED_SQL = _MEDICATIONS_SQL + "ORDER BY m.embedding <=> $1 LIMIT $2"

_MEDICATIONS_FILTERED_SQL = (
    _MEDICATIONS_SQL + "WHERE c.name ILIKE '%' || $3 || '%' "
    "ORDER BY m.embedding <=> $1 LIMIT $2"
)


async def query_conditions(pool, embedding: list[float], limit: int = 5) -> list[ConditionMatch]:
    rows = await pool.fetch(_CONDITIONS_SQL, embedding, limit)
    return [ConditionMatch(name=row["name"], distance=row["distance"]) for row in rows]


async def query_medications(
    pool, embedding: list[float], condition: str | None = None, limit: int = 5
) -> list[MedicationMatch]:
    """Cosine-similarity search over medications. If `condition` is given, first tries a
    substring match against the condition name; if that filter matches nothing (e.g. the
    agent guessed a phrasing that doesn't hit any stored condition), falls back to an
    unfiltered search rather than returning an empty result."""
    if condition:
        rows = await pool.fetch(_MEDICATIONS_FILTERED_SQL, embedding, limit, condition)
        if rows:
            return [_row_to_medication_match(row) for row in rows]

    rows = await pool.fetch(_MEDICATIONS_UNFILTERED_SQL, embedding, limit)
    return [_row_to_medication_match(row) for row in rows]


def _row_to_medication_match(row) -> MedicationMatch:
    return MedicationMatch(
        med_name=row["med_name"],
        generic_name=row["generic_name"],
        drug_content=row["drug_content"],
        drug_manufacturer=row["drug_manufacturer"],
        price=row["price"],
        prescription_required=row["prescription_required"],
        condition_name=row["condition_name"],
        distance=row["distance"],
    )
