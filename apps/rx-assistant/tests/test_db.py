from rx_assistant.db import MedicationMatch, build_medication_embedding_text, query_medications


def test_build_medication_embedding_text_joins_nonempty_fields() -> None:
    text = build_medication_embedding_text(
        "Vyvanse", "lisdexamfetamine", "CNS stimulants", "insomnia, dry mouth"
    )
    assert text == "Vyvanse lisdexamfetamine CNS stimulants insomnia, dry mouth"


def test_build_medication_embedding_text_skips_empty_fields() -> None:
    text = build_medication_embedding_text("Vyvanse", "", None, "")
    assert text == "Vyvanse"


class FakePool:
    def __init__(self, rows):
        self._rows = rows
        self.calls: list[tuple[str, tuple]] = []

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        return self._rows


def _sample_row() -> dict:
    return {
        "med_name": "Vyvanse",
        "med_url": "https://www.drugs.com/vyvanse.html",
        "generic_name": "lisdexamfetamine",
        "brand_names": "Vyvanse",
        "drug_classes": "CNS stimulants",
        "side_effects": "insomnia, dry mouth",
        "rx_otc": "Rx",
        "pregnancy_category": "C",
        "csa": "2",
        "alcohol": "X",
        "condition_name": "ADHD",
        "distance": 0.05,
    }


async def test_query_medications_filters_by_condition_when_matches_found() -> None:
    row = _sample_row()
    pool = FakePool([row])

    results = await query_medications(pool, [0.1, 0.2, 0.3], condition="ADHD", limit=5)

    assert results == [MedicationMatch(**row)]
    query, args = pool.calls[0]
    assert "FROM medications" in query
    assert args == ([0.1, 0.2, 0.3], 5, "ADHD")


async def test_query_medications_falls_back_when_condition_filter_finds_nothing() -> None:
    row = _sample_row()

    class FallbackPool(FakePool):
        async def fetch(self, query, *args):
            self.calls.append((query, args))
            return [] if len(args) == 3 else [row]

    pool = FallbackPool([])

    results = await query_medications(pool, [0.1, 0.2, 0.3], condition="Nonexistent", limit=5)

    assert results == [MedicationMatch(**row)]
    assert len(pool.calls) == 2  # filtered attempt, then unfiltered fallback
