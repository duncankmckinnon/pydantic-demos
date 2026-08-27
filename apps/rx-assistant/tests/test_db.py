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
