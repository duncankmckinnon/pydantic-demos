from rx_assistant.db import (
    ConditionMatch,
    MedicationMatch,
    build_medication_embedding_text,
    clean_condition_name,
    query_conditions,
    query_medications,
)


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
