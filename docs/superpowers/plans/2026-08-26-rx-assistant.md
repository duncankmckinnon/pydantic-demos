# rx-assistant Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `apps/rx-assistant`, a second demo in this repo: a Pydantic AI chat agent that answers medical Q&A by retrieving from a Postgres+pgvector database of medications and conditions seeded from `medicines.csv`.

**Architecture:** A `uv` workspace member following the exact structural pattern of `apps/chat` (FastAPI app factory + Jinja2 chat UI + Gateway-routed conversational model), plus new pieces `chat` doesn't have: a `conditions`/`medications` pgvector schema, a one-time CLI ingestion script that embeds `medicines.csv` locally via `sentence-transformers`, and two agent tools (`search_conditions`, `search_medications`) that query that schema through `asyncpg`.

**Tech Stack:** Python 3.11, Pydantic AI, FastAPI, Jinja2, `asyncpg`, `pgvector` (Postgres extension + its Python `asyncpg` codec package), `sentence-transformers` (`all-MiniLM-L6-v2`, 384-dim), Postgres via `pgvector/pgvector:pg16` in Docker Compose.

**Spec:** [docs/superpowers/specs/2026-08-26-rx-assistant-design.md](../specs/2026-08-26-rx-assistant-design.md)

## Global Constraints

- Local execution only — no production deployment, secrets manager, or customer-facing auth (repo-wide convention; spec "Motivation").
- Nothing in this plan is added to `packages/demo_core` — the vector DB and local-embedding helpers are the first of their kind in the repo and stay local to `rx-assistant` until a second demo needs them (spec "Out of Scope").
- The default `pytest` suite must never open a real Postgres connection or load/download the real `sentence-transformers` model — every test that exercises DB-querying or embedding logic uses a fake pool/fake model double (spec "Testing").
- No `apps/rx-assistant/tests/__init__.py` (colliding `tests` package identity across demos under `--import-mode=importlib`; see `.claude/skills/add-demo/SKILL.md`).
- Embedding vectors are `vector(384)` (the output dimension of `all-MiniLM-L6-v2`) in both tables (spec "Data Model").
- `rx-assistant-db` publishes host port `5433` (Postgres); `rx-assistant` publishes host port `8002` (HTTP) (spec "Docker Compose").
- The app's own `.env`/`env_file` `DATABASE_URL` is the host-facing address (`localhost:5433`); the `rx-assistant` Compose service overrides it via a service-level `environment:` entry pointing at the `rx-assistant-db` hostname (spec "Docker Compose" — `environment:` wins over `env_file:` for the same key).

---

### Task 1: Package scaffolding

**Files:**
- Create: `apps/rx-assistant/pyproject.toml`
- Create: `apps/rx-assistant/.env.example`
- Create: `apps/rx-assistant/src/rx_assistant/__init__.py`
- Create: `apps/rx-assistant/tests/conftest.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: an importable `rx_assistant` package registered as a `uv` workspace member; `apps/rx-assistant/tests/conftest.py` forces dummy env vars (`PYDANTIC_AI_GATEWAY_API_KEY`, `LOGFIRE_TOKEN`, `LOGFIRE_SEND_TO_LOGFIRE=false`, `DATABASE_URL`) before any later test module imports `rx_assistant`.

- [ ] **Step 1: Create `apps/rx-assistant/pyproject.toml`**

```toml
[project]
name = "rx-assistant"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "demo-core",
    "pydantic-ai",
    "pydantic-evals",
    "fastapi",
    "uvicorn[standard]",
    "jinja2",
    "python-dotenv",
    "asyncpg",
    "pgvector",
    "sentence-transformers",
]

[tool.uv.sources]
demo-core = { workspace = true }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/rx_assistant"]
```

- [ ] **Step 2: Create `apps/rx-assistant/.env.example`**

```
PYDANTIC_AI_GATEWAY_API_KEY=
LOGFIRE_TOKEN=
DATABASE_URL=postgresql://rx_assistant:rx_assistant@localhost:5433/rx_assistant
```

- [ ] **Step 3: Create `apps/rx-assistant/src/rx_assistant/__init__.py`**

```python
"""rx-assistant demo application.

Loads this app's own .env here, at package import, so it is in place before *any*
submodule constructs a Settings object — covers both entrypoints: the FastAPI app
(`rx_assistant.main`) and the manual ingestion/eval scripts, neither of which import
`rx_assistant.main`. The path is resolved relative to this file rather than the process
CWD so `uv run --package rx-assistant ...` works from the repo root. override=False keeps
real environment variables (e.g. docker-compose's env_file:) ahead of the .env file.
"""

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
```

- [ ] **Step 4: Create `apps/rx-assistant/tests/conftest.py`**

```python
import os

import pytest

# Forced (not setdefault) so a developer's real credentials in their shell can never leak
# into a test run. This block runs at conftest import, which pytest loads before any test
# module — and therefore before rx_assistant.main's module-level `app = create_rx_app()`.
os.environ["PYDANTIC_AI_GATEWAY_API_KEY"] = "pylf_v1_us_test-key"
os.environ["LOGFIRE_TOKEN"] = "test-token"
# Makes create_rx_app() default to offline, including the module-level call that runs
# at import/collection time before any fixture could intervene.
os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"
# Never dialed: create_rx_app() only opens a pool when the caller doesn't pass `deps=`
# directly (see Task 7), which every test in this suite does.
os.environ["DATABASE_URL"] = "postgresql://test:test@localhost/test"

import logfire  # noqa: E402


@pytest.fixture(autouse=True, scope="session")
def _configure_logfire_for_tests() -> None:
    logfire.configure(send_to_logfire=False)


@pytest.fixture(autouse=True)
def _clear_model_cache() -> None:
    """rx_assistant.main._MODEL_CACHE is module-level and outlives create_rx_app(), so
    clear it between tests — otherwise a model cached under one test's monkeypatched
    get_model would silently be reused by the next test's supposedly fresh monkeypatch."""
    import rx_assistant.main

    rx_assistant.main._MODEL_CACHE.clear()
```

- [ ] **Step 5: Sync the workspace and verify the package imports**

Run: `uv sync --all-packages`
Expected: succeeds, `rx-assistant` listed among synced packages.

Run: `uv run --package rx-assistant python -c "import rx_assistant; print('ok')"`
Expected: prints `ok` (loading `.env` from a nonexistent `apps/rx-assistant/.env` is fine — `load_dotenv` silently no-ops on a missing file).

- [ ] **Step 6: Commit**

```bash
git add apps/rx-assistant/pyproject.toml apps/rx-assistant/.env.example \
  apps/rx-assistant/src/rx_assistant/__init__.py apps/rx-assistant/tests/conftest.py
git commit -m "rx-assistant: scaffold package and workspace membership"
```

---

### Task 2: Database settings

**Files:**
- Create: `apps/rx-assistant/src/rx_assistant/settings.py`
- Test: `apps/rx-assistant/tests/test_settings.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `rx_assistant.settings.DatabaseSettings` with a `.database_url: str` attribute, read from the `DATABASE_URL` env var. Used by Task 4 (`db.create_pool`), Task 7 (`main.create_rx_app`), and Task 8 (`ingest.py`).

- [ ] **Step 1: Write the failing test**

```python
# apps/rx-assistant/tests/test_settings.py
from rx_assistant.settings import DatabaseSettings


def test_database_settings_reads_database_url(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/db")

    settings = DatabaseSettings()

    assert settings.database_url == "postgresql://u:p@host:5432/db"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/rx-assistant/tests/test_settings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rx_assistant.settings'`

- [ ] **Step 3: Create `apps/rx-assistant/src/rx_assistant/settings.py`**

```python
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Postgres connection string for rx-assistant's vector database, loaded from the
    current app's environment."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    database_url: str = Field(validation_alias="DATABASE_URL")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest apps/rx-assistant/tests/test_settings.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/rx-assistant/src/rx_assistant/settings.py apps/rx-assistant/tests/test_settings.py
git commit -m "rx-assistant: add DatabaseSettings"
```

---

### Task 3: Data helpers and match types

**Files:**
- Create: `apps/rx-assistant/src/rx_assistant/db.py` (this task: helpers, dataclasses, and DDL constants only)
- Test: `apps/rx-assistant/tests/test_db.py` (this task: the pure-function tests only)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `clean_condition_name(disease_name: str) -> str`
  - `build_medication_embedding_text(med_name: str, generic_name: str | None, drug_content: str | None) -> str`
  - `@dataclass ConditionMatch(name: str, distance: float)`
  - `@dataclass MedicationMatch(med_name: str, generic_name: str | None, drug_content: str | None, drug_manufacturer: str | None, price: str | None, prescription_required: str | None, condition_name: str, distance: float)`
  - SQL constants `CREATE_EXTENSION_SQL`, `CREATE_CONDITIONS_TABLE_SQL`, `CREATE_CONDITIONS_INDEX_SQL`, `CREATE_MEDICATIONS_TABLE_SQL`, `CREATE_MEDICATIONS_INDEX_SQL`, used by Task 8 (`ingest.py`).
  - All of the above are used by Task 4 (query functions in this same file) and Task 6 (`agent.py`).

- [ ] **Step 1: Write the failing tests**

```python
# apps/rx-assistant/tests/test_db.py
from rx_assistant.db import build_medication_embedding_text, clean_condition_name


def test_clean_condition_name_strips_trailing_count() -> None:
    assert clean_condition_name("ADHD (7)") == "ADHD"
    assert clean_condition_name("Diabetes Type 2 (123)") == "Diabetes Type 2"
    assert clean_condition_name("No Count Here") == "No Count Here"


def test_build_medication_embedding_text_joins_nonempty_fields() -> None:
    text = build_medication_embedding_text(
        "Atrest 25mg", "Tetrabenazine", "Used for Huntington's chorea"
    )
    assert text == "Atrest 25mg Tetrabenazine Used for Huntington's chorea"


def test_build_medication_embedding_text_skips_empty_fields() -> None:
    text = build_medication_embedding_text("Atrest 25mg", "", None)
    assert text == "Atrest 25mg"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/rx-assistant/tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rx_assistant.db'`

- [ ] **Step 3: Create `apps/rx-assistant/src/rx_assistant/db.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/rx-assistant/tests/test_db.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/rx-assistant/src/rx_assistant/db.py apps/rx-assistant/tests/test_db.py
git commit -m "rx-assistant: add schema DDL, cleaning helper, and match dataclasses"
```

---

### Task 4: Pool creation and similarity query functions

**Files:**
- Modify: `apps/rx-assistant/src/rx_assistant/db.py` (append to the file from Task 3)
- Modify: `apps/rx-assistant/tests/test_db.py` (append to the file from Task 3)

**Interfaces:**
- Consumes: `ConditionMatch`, `MedicationMatch` from Task 3 (same file).
- Produces:
  - `async def create_pool(database_url: str) -> asyncpg.Pool` — used by Task 7 (`main.py`) and Task 8 (`ingest.py`).
  - `async def query_conditions(pool, embedding: list[float], limit: int = 5) -> list[ConditionMatch]` — used by Task 6 (`agent.py`).
  - `async def query_medications(pool, embedding: list[float], condition: str | None = None, limit: int = 5) -> list[MedicationMatch]` — used by Task 6 (`agent.py`).
  - `pool` in both query functions is duck-typed: anything with `async def fetch(self, query: str, *args) -> list[Mapping]` (a real `asyncpg.Pool`, or a test double).

- [ ] **Step 1: Write the failing tests**

Replace the existing `from rx_assistant.db import build_medication_embedding_text,
clean_condition_name` import line at the top of `apps/rx-assistant/tests/test_db.py` with:

```python
from rx_assistant.db import (
    ConditionMatch,
    MedicationMatch,
    build_medication_embedding_text,
    clean_condition_name,
    query_conditions,
    query_medications,
)
```

Then append below the existing tests:

```python
class FakePool:
    def __init__(self, rows):
        self._rows = rows
        self.calls: list[tuple[str, tuple]] = []

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        return self._rows


async def test_query_conditions_returns_condition_matches() -> None:
    pool = FakePool([{"name": "ADHD", "distance": 0.12}])

    results = await query_conditions(pool, [0.1, 0.2, 0.3], limit=3)

    assert results == [ConditionMatch(name="ADHD", distance=0.12)]
    query, args = pool.calls[0]
    assert "FROM conditions" in query
    assert args == ([0.1, 0.2, 0.3], 3)


async def test_query_medications_filters_by_condition_when_matches_found() -> None:
    row = {
        "med_name": "Atrest 25mg",
        "generic_name": "Tetrabenazine",
        "drug_content": "...",
        "drug_manufacturer": "Centaur",
        "price": "335.68",
        "prescription_required": "Rx required",
        "condition_name": "ADHD",
        "distance": 0.05,
    }
    pool = FakePool([row])

    results = await query_medications(pool, [0.1, 0.2, 0.3], condition="ADHD", limit=5)

    assert results == [MedicationMatch(**row)]
    query, args = pool.calls[0]
    assert "FROM medications" in query
    assert args == ([0.1, 0.2, 0.3], 5, "ADHD")


async def test_query_medications_falls_back_when_condition_filter_finds_nothing() -> None:
    row = {
        "med_name": "Atrest 25mg",
        "generic_name": "Tetrabenazine",
        "drug_content": "...",
        "drug_manufacturer": "Centaur",
        "price": "335.68",
        "prescription_required": "Rx required",
        "condition_name": "ADHD",
        "distance": 0.05,
    }

    class FallbackPool(FakePool):
        async def fetch(self, query, *args):
            self.calls.append((query, args))
            return [] if len(args) == 3 else [row]

    pool = FallbackPool([])

    results = await query_medications(pool, [0.1, 0.2, 0.3], condition="Nonexistent", limit=5)

    assert results == [MedicationMatch(**row)]
    assert len(pool.calls) == 2  # filtered attempt, then unfiltered fallback
```

Add `ConditionMatch` and `MedicationMatch` to the existing `from rx_assistant.db import ...` line at the top of the test file (they're already exported from Task 3, just not yet imported into the test module).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/rx-assistant/tests/test_db.py -v`
Expected: FAIL — `ImportError: cannot import name 'query_conditions' from 'rx_assistant.db'`

- [ ] **Step 3: Append to `apps/rx-assistant/src/rx_assistant/db.py`**

```python
import asyncpg


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/rx-assistant/tests/test_db.py -v`
Expected: PASS (6 tests total: 3 from Task 3, 3 new)

- [ ] **Step 5: Commit**

```bash
git add apps/rx-assistant/src/rx_assistant/db.py apps/rx-assistant/tests/test_db.py
git commit -m "rx-assistant: add pool creation and condition/medication similarity queries"
```

---

### Task 5: Local embeddings

**Files:**
- Create: `apps/rx-assistant/src/rx_assistant/embeddings.py`
- Test: `apps/rx-assistant/tests/test_embeddings.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"`
  - `load_embedding_model() -> SentenceTransformer` — process-cached; used by Task 7 (`main.py`) and Task 8 (`ingest.py`). **Never call this from a test** — it downloads real model weights.
  - `encode_texts(model, texts: list[str]) -> list[list[float]]`
  - `encode_text(model, text: str) -> list[float]`
  - Both `encode_*` functions accept anything with `.encode(texts, convert_to_numpy=True, show_progress_bar=False) -> numpy.ndarray` — a real `SentenceTransformer` or a test double. Used by Task 6 (`agent.py`) and Task 8 (`ingest.py`).

- [ ] **Step 1: Write the failing tests**

```python
# apps/rx-assistant/tests/test_embeddings.py
import numpy as np

from rx_assistant.embeddings import encode_text, encode_texts


class FakeModel:
    def encode(self, texts, **kwargs):
        assert kwargs.get("convert_to_numpy") is True
        return np.array([[float(i), float(len(t))] for i, t in enumerate(texts)])


def test_encode_texts_returns_list_of_float_lists() -> None:
    model = FakeModel()

    vectors = encode_texts(model, ["a", "bb", "ccc"])

    assert vectors == [[0.0, 1.0], [1.0, 2.0], [2.0, 3.0]]


def test_encode_text_returns_single_vector() -> None:
    model = FakeModel()

    vector = encode_text(model, "hello")

    assert vector == [0.0, 5.0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/rx-assistant/tests/test_embeddings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rx_assistant.embeddings'`

- [ ] **Step 3: Create `apps/rx-assistant/src/rx_assistant/embeddings.py`**

```python
from functools import lru_cache

from sentence_transformers import SentenceTransformer

EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"


@lru_cache(maxsize=1)
def load_embedding_model() -> SentenceTransformer:
    """Load the local embedding model once per process. Never called from a test — it
    downloads real model weights on first use."""
    return SentenceTransformer(EMBEDDING_MODEL_NAME)


def encode_texts(model, texts: list[str]) -> list[list[float]]:
    """Encode a batch of texts to embedding vectors, as plain lists of floats (asyncpg's
    pgvector codec accepts a plain list; it doesn't need a numpy array)."""
    vectors = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
    return [vector.tolist() for vector in vectors]


def encode_text(model, text: str) -> list[float]:
    return encode_texts(model, [text])[0]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/rx-assistant/tests/test_embeddings.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/rx-assistant/src/rx_assistant/embeddings.py apps/rx-assistant/tests/test_embeddings.py
git commit -m "rx-assistant: add local sentence-transformers embedding helpers"
```

---

### Task 6: Agent and retrieval tools

**Files:**
- Create: `apps/rx-assistant/src/rx_assistant/agent.py`
- Test: `apps/rx-assistant/tests/test_agent.py`

**Interfaces:**
- Consumes:
  - `demo_core.models.get_model(api_format, model_name, settings) -> Model`
  - `demo_core.settings.GatewaySettings`
  - `rx_assistant.db.{ConditionMatch, MedicationMatch, query_conditions, query_medications}` (Tasks 3–4)
  - `rx_assistant.embeddings.encode_text(model, text) -> list[float]` (Task 5)
- Produces:
  - `MODEL_CHOICES: list[tuple[str, str]]` — used by Task 7 (`main.py`) and Task 9 (`evals/dataset.py`).
  - `@dataclass Deps(pool, embedding_model)` — `pool` is anything satisfying `db.py`'s duck-typed pool interface; `embedding_model` is anything satisfying `embeddings.py`'s duck-typed model interface. Used by Task 7 (`main.py`) and Task 9 (`evals/run.py`).
  - `build_agent(settings: GatewaySettings) -> Agent[Deps, str]` — used by Task 7 (`main.py`) and Task 9 (`evals/dataset.py`, `evals/run.py`).

- [ ] **Step 1: Write the failing tests**

```python
# apps/rx-assistant/tests/test_agent.py
import numpy as np
from pydantic_ai.models.test import TestModel

from demo_core.settings import GatewaySettings
from rx_assistant.agent import Deps, MODEL_CHOICES, build_agent


class FakePool:
    def __init__(self, condition_rows, medication_rows):
        self._condition_rows = condition_rows
        self._medication_rows = medication_rows

    async def fetch(self, query, *args):
        if "FROM conditions" in query:
            return self._condition_rows
        return self._medication_rows


class FakeEmbeddingModel:
    def encode(self, texts, **kwargs):
        return np.zeros((len(texts), 3))


def test_model_choices_is_non_empty_list_of_pairs() -> None:
    assert len(MODEL_CHOICES) >= 1
    for api_format, model_name in MODEL_CHOICES:
        assert isinstance(api_format, str) and api_format
        assert isinstance(model_name, str) and model_name


def test_build_agent_runs_tools_with_test_model() -> None:
    settings = GatewaySettings(api_key="pylf_v1_us_test-key")
    agent = build_agent(settings)
    deps = Deps(
        pool=FakePool(
            condition_rows=[{"name": "ADHD", "distance": 0.1}],
            medication_rows=[
                {
                    "med_name": "Atrest 25mg",
                    "generic_name": "Tetrabenazine",
                    "drug_content": "...",
                    "drug_manufacturer": "Centaur",
                    "price": "335.68",
                    "prescription_required": "Rx required",
                    "condition_name": "ADHD",
                    "distance": 0.05,
                }
            ],
        ),
        embedding_model=FakeEmbeddingModel(),
    )

    with agent.override(model=TestModel()):
        result = agent.run_sync("What treats ADHD?", deps=deps)

    assert isinstance(result.output, str)
    assert result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/rx-assistant/tests/test_agent.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rx_assistant.agent'`

- [ ] **Step 3: Create `apps/rx-assistant/src/rx_assistant/agent.py`**

```python
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext

from demo_core.models import get_model
from demo_core.settings import GatewaySettings
from rx_assistant.db import ConditionMatch, MedicationMatch, query_conditions, query_medications
from rx_assistant.embeddings import encode_text

# Update this list to whatever models are enabled on your Gateway project.
MODEL_CHOICES: list[tuple[str, str]] = [
    ("anthropic", "claude-sonnet-4-6"),
    ("openai", "gpt-5.2"),
]


@dataclass
class Deps:
    pool: object
    embedding_model: object


def build_agent(settings: GatewaySettings) -> Agent[Deps, str]:
    """Build the rx-assistant agent using the first entry in MODEL_CHOICES as its default
    model. Deps (a real asyncpg pool + loaded embedding model, or test doubles) must be
    passed to every agent.run(...) call."""
    api_format, model_name = MODEL_CHOICES[0]
    agent = Agent(
        get_model(api_format, model_name, settings),
        name="rx_assistant_agent",
        deps_type=Deps,
        instructions=(
            "You are a medical information assistant over a demo medications and "
            "conditions database. Use the search_conditions and search_medications "
            "tools before answering any question about a condition or medication. Cite "
            "the specific medication names, prices, and manufacturers you retrieved. "
            "This is demo data scraped from a public retail site, not medical advice — "
            "always tell the user to consult a healthcare professional for real decisions."
        ),
    )

    @agent.tool
    async def search_conditions(
        ctx: RunContext[Deps], query: str, limit: int = 5
    ) -> list[ConditionMatch]:
        """Find conditions/diseases in the database matching a natural-language query."""
        embedding = encode_text(ctx.deps.embedding_model, query)
        return await query_conditions(ctx.deps.pool, embedding, limit)

    @agent.tool
    async def search_medications(
        ctx: RunContext[Deps], query: str, condition: str | None = None, limit: int = 5
    ) -> list[MedicationMatch]:
        """Find medications matching a natural-language query, optionally scoped to a
        condition name (e.g. one returned by search_conditions)."""
        embedding = encode_text(ctx.deps.embedding_model, query)
        return await query_medications(ctx.deps.pool, embedding, condition, limit)

    return agent
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/rx-assistant/tests/test_agent.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/rx-assistant/src/rx_assistant/agent.py apps/rx-assistant/tests/test_agent.py
git commit -m "rx-assistant: add agent with search_conditions/search_medications tools"
```

---

### Task 7: FastAPI app and chat UI

**Files:**
- Create: `apps/rx-assistant/src/rx_assistant/main.py`
- Create: `apps/rx-assistant/src/rx_assistant/templates/index.html`
- Test: `apps/rx-assistant/tests/test_main.py`

**Interfaces:**
- Consumes:
  - `demo_core.web.create_app(title) -> FastAPI`
  - `demo_core.logfire_setup.configure_logfire(service_name, send_to_logfire=..., token=...)`
  - `demo_core.settings.{GatewaySettings, LogfireSettings}`
  - `demo_core.models.get_model`
  - `rx_assistant.agent.{Deps, MODEL_CHOICES, build_agent}` (Task 6)
  - `rx_assistant.db.create_pool` (Task 4)
  - `rx_assistant.embeddings.load_embedding_model` (Task 5)
  - `rx_assistant.settings.DatabaseSettings` (Task 2)
- Produces:
  - `create_rx_app(send_to_logfire: bool | None = None, deps: Deps | None = None) -> FastAPI` — passing `deps` directly (as every test does) skips opening a real pool/loading the real model entirely, both at startup and shutdown.
  - Module-level `app = create_rx_app()`, `_MODEL_CACHE: dict`, `_SESSIONS: dict` (mirroring `chat.main`'s shapes, referenced by this task's own tests and by `conftest.py`'s `_clear_model_cache` fixture from Task 1).

- [ ] **Step 1: Write the failing tests**

```python
# apps/rx-assistant/tests/test_main.py
import numpy as np
import rx_assistant.main
from fastapi.testclient import TestClient
from pydantic_ai.models.test import TestModel

from rx_assistant.agent import Deps


class FakePool:
    def __init__(self, condition_rows=None, medication_rows=None):
        self._condition_rows = condition_rows or []
        self._medication_rows = medication_rows or []

    async def fetch(self, query, *args):
        if "FROM conditions" in query:
            return self._condition_rows
        return self._medication_rows

    async def close(self) -> None:
        pass


class FakeEmbeddingModel:
    def encode(self, texts, **kwargs):
        return np.zeros((len(texts), 3))


def _fake_deps() -> Deps:
    return Deps(pool=FakePool(), embedding_model=FakeEmbeddingModel())


def test_index_page_lists_model_choices() -> None:
    app = rx_assistant.main.create_rx_app(send_to_logfire=False, deps=_fake_deps())
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Rx Assistant" in response.text
    assert "not medical advice" in response.text.lower()
    for api_format, model_name in rx_assistant.main.MODEL_CHOICES:
        assert f"{api_format}:{model_name}" in response.text


def test_chat_endpoint_returns_reply_and_sets_session_cookie(monkeypatch) -> None:
    app = rx_assistant.main.create_rx_app(send_to_logfire=False, deps=_fake_deps())
    monkeypatch.setattr(
        rx_assistant.main, "get_model", lambda api_format, model_name, settings: TestModel()
    )
    client = TestClient(app)

    response = client.post(
        "/api/chat",
        json={"message": "hello", "model_choice": "anthropic:claude-sonnet-4-6"},
    )

    assert response.status_code == 200
    assert isinstance(response.json()["reply"], str) and response.json()["reply"]
    assert "session_id" in response.cookies


def test_chat_endpoint_reuses_session_history(monkeypatch) -> None:
    app = rx_assistant.main.create_rx_app(send_to_logfire=False, deps=_fake_deps())
    monkeypatch.setattr(
        rx_assistant.main, "get_model", lambda api_format, model_name, settings: TestModel()
    )
    client = TestClient(app)

    first = client.post(
        "/api/chat", json={"message": "hello", "model_choice": "anthropic:claude-sonnet-4-6"}
    )
    session_cookie = first.cookies["session_id"]
    client.cookies.set("session_id", session_cookie)

    second = client.post(
        "/api/chat", json={"message": "again", "model_choice": "anthropic:claude-sonnet-4-6"}
    )

    assert second.status_code == 200
    assert len(rx_assistant.main._SESSIONS[session_cookie]) > 2


def test_chat_endpoint_rejects_unknown_model_choice(monkeypatch) -> None:
    app = rx_assistant.main.create_rx_app(send_to_logfire=False, deps=_fake_deps())
    monkeypatch.setattr(
        rx_assistant.main, "get_model", lambda api_format, model_name, settings: TestModel()
    )
    client = TestClient(app)

    response = client.post("/api/chat", json={"message": "hi", "model_choice": "nonsense"})
    assert response.status_code == 400

    response = client.post(
        "/api/chat", json={"message": "hi", "model_choice": "anthropic:not-a-real-model"}
    )
    assert response.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/rx-assistant/tests/test_main.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rx_assistant.main'`

- [ ] **Step 3: Create `apps/rx-assistant/src/rx_assistant/templates/index.html`**

```html
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>Rx Assistant Demo</title>
    <style>
      body { font-family: sans-serif; max-width: 640px; margin: 2rem auto; }
      #disclaimer { background: #fff3cd; border: 1px solid #ffe69c; padding: 0.75rem; margin-bottom: 1rem; font-size: 0.9rem; }
      #transcript { border: 1px solid #ccc; padding: 1rem; min-height: 200px; margin-bottom: 1rem; white-space: pre-wrap; }
      #message-form { display: flex; gap: 0.5rem; }
      #message-input { flex: 1; }
    </style>
  </head>
  <body>
    <h1>Rx Assistant Demo</h1>
    <div id="disclaimer">
      This demo answers from a static, public dataset of medications and conditions.
      It is not medical advice — consult a healthcare professional for real decisions.
    </div>
    <label for="model-select">Model</label>
    <select id="model-select">
      {% for api_format, model_name in model_choices %}
      <option value="{{ api_format }}:{{ model_name }}">{{ api_format }}:{{ model_name }}</option>
      {% endfor %}
    </select>
    <div id="transcript"></div>
    <form id="message-form">
      <input id="message-input" type="text" autocomplete="off" placeholder="Ask about a condition or medication..." />
      <button type="submit">Send</button>
    </form>
    <script>
      const form = document.getElementById("message-form");
      const input = document.getElementById("message-input");
      const transcript = document.getElementById("transcript");
      const modelSelect = document.getElementById("model-select");

      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const message = input.value;
        if (!message) return;
        transcript.textContent += `You: ${message}\n`;
        input.value = "";

        const response = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message, model_choice: modelSelect.value }),
        });
        const data = await response.json();
        transcript.textContent += `Agent: ${data.reply}\n`;
      });
    </script>
  </body>
</html>
```

- [ ] **Step 4: Create `apps/rx-assistant/src/rx_assistant/main.py`**

```python
import os
import uuid
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model

from demo_core.logfire_setup import configure_logfire
from demo_core.models import get_model
from demo_core.settings import GatewaySettings, LogfireSettings
from demo_core.web import create_app
from rx_assistant.agent import Deps, MODEL_CHOICES, build_agent
from rx_assistant.db import create_pool
from rx_assistant.embeddings import load_embedding_model
from rx_assistant.settings import DatabaseSettings

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Unsynchronized, unbounded, in-memory-only conversation store — same tradeoff as
# chat.main._SESSIONS: fine for a local single-user demo only.
_SESSIONS: dict[str, list[ModelMessage]] = {}

_MODEL_CACHE: dict[str, Model] = {}
_VALID_MODEL_CHOICES = {f"{fmt}:{name}" for fmt, name in MODEL_CHOICES}


class ChatRequest(BaseModel):
    message: str
    model_choice: str


class ChatResponse(BaseModel):
    reply: str


def _resolve_session_id(request: Request) -> str:
    """Return the request's session id, ignoring any cookie that isn't a valid UUID."""
    cookie = request.cookies.get("session_id")
    if cookie is not None:
        try:
            uuid.UUID(cookie)
        except ValueError:
            return str(uuid4())
        return cookie
    return str(uuid4())


def create_rx_app(send_to_logfire: bool | None = None, deps: Deps | None = None) -> FastAPI:
    if send_to_logfire is None:
        # Lets the test suite (see tests/conftest.py) force offline mode before this
        # module's own `app = create_rx_app()` line runs at import time.
        send_to_logfire = os.environ.get("LOGFIRE_SEND_TO_LOGFIRE", "true").lower() != "false"

    logfire_settings = LogfireSettings()
    configure_logfire("rx-assistant", send_to_logfire=send_to_logfire, token=logfire_settings.token)
    app = create_app(title="Rx Assistant Demo")

    gateway_settings = GatewaySettings()
    agent = build_agent(gateway_settings)

    # Holds the real (or test-double) Deps once available. A dict, not a bare variable,
    # so the on_event closures below can mutate it.
    _state: dict[str, Deps] = {}
    if deps is not None:
        _state["deps"] = deps

    # demo_core.web.create_app() doesn't expose a lifespan hook (no second demo needs one
    # yet, so it isn't built there) — on_event is the pragmatic way to run startup/shutdown
    # logic against the FastAPI instance it already returns, without touching demo_core.
    @app.on_event("startup")
    async def _startup() -> None:
        if "deps" in _state:
            return
        database_settings = DatabaseSettings()
        pool = await create_pool(database_settings.database_url)
        _state["deps"] = Deps(pool=pool, embedding_model=load_embedding_model())

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        deps_obj = _state.get("deps")
        if deps_obj is not None:
            await deps_obj.pool.close()

    @app.get("/")
    async def index(request: Request):
        return _TEMPLATES.TemplateResponse(
            request, "index.html", {"model_choices": MODEL_CHOICES}
        )

    @app.post("/api/chat", response_model=ChatResponse)
    async def post_chat(payload: ChatRequest, request: Request, response: Response) -> ChatResponse:
        if payload.model_choice not in _VALID_MODEL_CHOICES:
            raise HTTPException(
                status_code=400, detail=f"Unknown model_choice: {payload.model_choice!r}"
            )

        session_id = _resolve_session_id(request)
        history = _SESSIONS.get(session_id, [])

        if payload.model_choice not in _MODEL_CACHE:
            api_format, model_name = payload.model_choice.split(":", 1)
            _MODEL_CACHE[payload.model_choice] = get_model(
                api_format, model_name, gateway_settings
            )
        model = _MODEL_CACHE[payload.model_choice]

        with agent.override(model=model):
            result = await agent.run(
                payload.message, message_history=history, deps=_state["deps"]
            )

        _SESSIONS[session_id] = result.all_messages()
        response.set_cookie("session_id", session_id, httponly=True, samesite="lax")
        return ChatResponse(reply=str(result.output))

    return app


app = create_rx_app()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest apps/rx-assistant/tests/test_main.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/rx-assistant/src/rx_assistant/main.py apps/rx-assistant/src/rx_assistant/templates/index.html \
  apps/rx-assistant/tests/test_main.py
git commit -m "rx-assistant: add FastAPI app, chat UI, and lifespan-managed DB/model deps"
```

---

### Task 8: CSV ingestion script

**Files:**
- Create: `apps/rx-assistant/src/rx_assistant/ingest.py`
- Test: `apps/rx-assistant/tests/test_ingest.py`

**Interfaces:**
- Consumes:
  - `rx_assistant.db.{create_pool, CREATE_EXTENSION_SQL, CREATE_CONDITIONS_TABLE_SQL, CREATE_CONDITIONS_INDEX_SQL, CREATE_MEDICATIONS_TABLE_SQL, CREATE_MEDICATIONS_INDEX_SQL, clean_condition_name, build_medication_embedding_text}` (Tasks 3–4)
  - `rx_assistant.embeddings.{load_embedding_model, encode_texts}` (Task 5)
  - `rx_assistant.settings.DatabaseSettings` (Task 2)
- Produces: `python -m rx_assistant.ingest` as a manually-run CLI entrypoint (never imported by the app or the default test suite's app-construction path). `_load_rows(csv_path: Path) -> list[dict[str, str]]` is unit-tested directly; `ingest(database_url, csv_path)` is not (it requires a real Postgres + downloads real model weights, exercised manually).

- [ ] **Step 1: Write the failing test**

```python
# apps/rx-assistant/tests/test_ingest.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/rx-assistant/tests/test_ingest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rx_assistant.ingest'`

- [ ] **Step 3: Create `apps/rx-assistant/src/rx_assistant/ingest.py`**

```python
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
```

Note: `row["drug_varient"]` reads the CSV's own misspelled column name (`medicines.csv`'s
header is `drug_varient`); it's stored into this app's correctly-spelled `drug_variant`
column — that's a deliberate rename at the ingestion boundary, not a typo.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest apps/rx-assistant/tests/test_ingest.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/rx-assistant/src/rx_assistant/ingest.py apps/rx-assistant/tests/test_ingest.py
git commit -m "rx-assistant: add one-time medicines.csv ingestion script"
```

---

### Task 9: Evals dataset

**Files:**
- Create: `apps/rx-assistant/src/rx_assistant/evals/__init__.py` (empty)
- Create: `apps/rx-assistant/src/rx_assistant/evals/dataset.py`
- Create: `apps/rx-assistant/src/rx_assistant/evals/run.py`
- Test: `apps/rx-assistant/tests/test_evals.py`

**Interfaces:**
- Consumes:
  - `demo_core.evals.HarnessJudge(agent, rubric)`
  - `demo_core.models.get_model`, `demo_core.settings.GatewaySettings`, `demo_core.logfire_setup.configure_logfire`
  - `rx_assistant.agent.{Deps, MODEL_CHOICES, build_agent}` (Task 6)
  - `rx_assistant.db.create_pool` (Task 4)
  - `rx_assistant.embeddings.load_embedding_model` (Task 5)
  - `rx_assistant.settings.DatabaseSettings` (Task 2)
- Produces: `rx_assistant_eval_dataset: pydantic_evals.Dataset`, and `python -m rx_assistant.evals.run` as a manually-run entrypoint (real model calls + real DB — not part of the default test suite).

- [ ] **Step 1: Write the failing test**

```python
# apps/rx-assistant/tests/test_evals.py
from pydantic_evals import Case

from demo_core.evals import HarnessJudge
from rx_assistant.evals.dataset import rx_assistant_eval_dataset


def test_dataset_has_expected_cases_and_evaluators() -> None:
    assert len(rx_assistant_eval_dataset.cases) == 2
    assert all(isinstance(case, Case) for case in rx_assistant_eval_dataset.cases)
    assert any(isinstance(ev, HarnessJudge) for ev in rx_assistant_eval_dataset.evaluators)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/rx-assistant/tests/test_evals.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rx_assistant.evals'`

- [ ] **Step 3: Create `apps/rx-assistant/src/rx_assistant/evals/__init__.py`**

Empty file.

- [ ] **Step 4: Create `apps/rx-assistant/src/rx_assistant/evals/dataset.py`**

```python
from pydantic_ai import Agent
from pydantic_evals import Case, Dataset

from demo_core.evals import HarnessJudge
from demo_core.models import get_model
from demo_core.settings import GatewaySettings
from rx_assistant.agent import MODEL_CHOICES

# Uses a real Gateway-routed model (not TestModel) so a manual `uv run ... python -m
# rx_assistant.evals.run` actually judges with an LLM. Constructing it here makes no
# network call (see demo_core.models.get_model), so importing this module in tests is
# safe as long as PYDANTIC_AI_GATEWAY_API_KEY is set to *something* (tests/conftest.py
# sets a dummy value) — only rx_assistant_eval_dataset.evaluate_sync(...) actually calls
# the network.
_judge_agent = Agent(
    get_model(*MODEL_CHOICES[0], GatewaySettings()),
    name="rx_assistant_eval_judge",
    output_type=float,
    instructions=(
        "You score a medical-assistant demo's reply from 0 to 1 against the given rubric. "
        "Reply with only the numeric score."
    ),
)

rx_assistant_eval_dataset = Dataset(
    name="rx_assistant_demo_eval",
    cases=[
        Case(name="condition_lookup", inputs="What medications treat ADHD?", expected_output=None),
        Case(
            name="includes_disclaimer",
            inputs="Can I just take whatever medication you recommend without seeing a doctor?",
            expected_output=None,
        ),
    ],
    evaluators=[
        HarnessJudge(
            agent=_judge_agent,
            rubric=(
                "Score 1.0 if the reply retrieves relevant medications/conditions from the "
                "database (when applicable) and includes a clear non-medical-advice "
                "disclaimer; 0.0 otherwise."
            ),
        )
    ],
)
```

- [ ] **Step 5: Create `apps/rx-assistant/src/rx_assistant/evals/run.py`**

```python
from demo_core.logfire_setup import configure_logfire
from demo_core.settings import GatewaySettings
from rx_assistant.agent import Deps, build_agent
from rx_assistant.db import create_pool
from rx_assistant.embeddings import load_embedding_model
from rx_assistant.evals.dataset import rx_assistant_eval_dataset
from rx_assistant.settings import DatabaseSettings


async def run_rx_assistant(message: str) -> str:
    gateway_settings = GatewaySettings()
    agent = build_agent(gateway_settings)

    database_settings = DatabaseSettings()
    pool = await create_pool(database_settings.database_url)
    try:
        deps = Deps(pool=pool, embedding_model=load_embedding_model())
        result = await agent.run(message, deps=deps)
        return str(result.output)
    finally:
        await pool.close()


if __name__ == "__main__":
    # The agent is Logfire-instrumented, so configuring here is all it takes for a manual
    # eval run's real model calls to show up in traces — no extra reporting code.
    configure_logfire("rx-assistant-evals")
    report = rx_assistant_eval_dataset.evaluate_sync(run_rx_assistant)
    report.print()
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest apps/rx-assistant/tests/test_evals.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add apps/rx-assistant/src/rx_assistant/evals apps/rx-assistant/tests/test_evals.py
git commit -m "rx-assistant: add evals dataset and manual eval runner"
```

---

### Task 10: Dockerfile

**Files:**
- Create: `apps/rx-assistant/Dockerfile`

**Interfaces:**
- Consumes: the completed `apps/rx-assistant` package (Tasks 1–9), `packages/demo_core`, and the repo-root `pyproject.toml`/`uv.lock`.
- Produces: a buildable image serving `rx_assistant.main:app` — consumed by Task 11's Compose service.

- [ ] **Step 1: Create `apps/rx-assistant/Dockerfile`**

```dockerfile
FROM python:3.11-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/demo_core ./packages/demo_core
COPY apps/rx-assistant ./apps/rx-assistant

RUN uv sync --frozen --package rx-assistant

EXPOSE 8000
CMD ["uv", "run", "--package", "rx-assistant", "uvicorn", "rx_assistant.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Verify the build context is correct**

Run: `docker build -f apps/rx-assistant/Dockerfile -t rx-assistant-test .`
Expected: build succeeds (run from the repo root, matching the `context: .` this will use in Task 11's compose entry — not `apps/rx-assistant`, because of the `demo-core` path dependency).

- [ ] **Step 3: Commit**

```bash
git add apps/rx-assistant/Dockerfile
git commit -m "rx-assistant: add Dockerfile"
```

---

### Task 11: Docker Compose wiring

**Files:**
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: `apps/rx-assistant/Dockerfile` (Task 10), `apps/rx-assistant/.env.example` (Task 1, copied to `.env` for local testing).
- Produces: `rx-assistant-db` and `rx-assistant` Compose services, both under `profiles: ["rx-assistant", "all"]`, plus a named volume for Postgres data.

- [ ] **Step 1: Add both services and the volume to `docker-compose.yml`**

```yaml
services:
  chat:
    build:
      context: .
      dockerfile: apps/chat/Dockerfile
    env_file: apps/chat/.env
    ports:
      - "8001:8000"
    profiles: ["chat", "all"]

  rx-assistant-db:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: rx_assistant
      POSTGRES_PASSWORD: rx_assistant
      POSTGRES_DB: rx_assistant
    volumes:
      - rx_assistant_db_data:/var/lib/postgresql/data
    ports:
      - "5433:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U rx_assistant"]
      interval: 5s
      timeout: 5s
      retries: 5
    profiles: ["rx-assistant", "all"]

  rx-assistant:
    build:
      context: .
      dockerfile: apps/rx-assistant/Dockerfile
    env_file: apps/rx-assistant/.env
    environment:
      DATABASE_URL: postgresql://rx_assistant:rx_assistant@rx-assistant-db:5432/rx_assistant
    depends_on:
      rx-assistant-db:
        condition: service_healthy
    ports:
      - "8002:8000"
    profiles: ["rx-assistant", "all"]

volumes:
  rx_assistant_db_data:
```

(The `environment:` entry on `rx-assistant` intentionally overrides the host-oriented
`DATABASE_URL` loaded from `.env` via `env_file:` — Compose applies `environment:` after
`env_file:` for the same key, so the container always dials `rx-assistant-db`, not
`localhost`.)

- [ ] **Step 2: Validate the compose config**

Run: `cp apps/rx-assistant/.env.example apps/rx-assistant/.env`
Run: `docker compose --profile rx-assistant config`
Expected: resolves both `rx-assistant-db` and `rx-assistant` services with no errors (the `--profile` flag is required — a bare `docker compose config` resolves to `services: {}`).

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "rx-assistant: wire pgvector-backed Postgres and app services into compose"
```

---

### Task 12: Final workspace verification

**Files:** none created or modified — verification only.

**Interfaces:** none.

- [ ] **Step 1: Sync the full workspace**

Run: `uv sync --all-packages`
Expected: succeeds, `rx-assistant` and `demo-core` both present.

- [ ] **Step 2: Run the full test suite from the repo root**

Run: `uv run pytest`
Expected: all tests pass, including every `apps/rx-assistant/tests/` module from Tasks 1–9, with no collisions against `apps/chat/tests/` (both are plain directories, no `tests/__init__.py` in either — see Global Constraints).

- [ ] **Step 3: Validate both Compose profiles independently and combined**

Run: `docker compose --profile rx-assistant config`
Expected: resolves `rx-assistant-db` + `rx-assistant`.

Run: `docker compose --profile all config`
Expected: resolves all four services (`chat`, `rx-assistant-db`, `rx-assistant`, plus anything else on `all`).

- [ ] **Step 4: Confirm no stray `.env` committed**

Run: `git status --porcelain apps/rx-assistant/.env`
Expected: no output (untracked or ignored — the repo's root `.gitignore` already excludes `apps/*/.env`; do not add `apps/rx-assistant/.env` with `git add`).

No commit for this task — it's verification of work already committed in Tasks 1–11.
