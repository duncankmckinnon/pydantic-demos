import re
from dataclasses import dataclass

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
