from pathlib import Path


README = Path(__file__).resolve().parents[1] / "README.md"


def test_readme_preserves_logfire_resource_names_and_credential_roles() -> None:
    text = README.read_text()

    assert "duncan/applicant-support" in text
    assert "applicant_support_baseline" in text
    assert "applicant_support_prompt" in text
    assert "applicant_support_model" in text
    assert "LOGFIRE_TOKEN" in text and "telemetry" in text
    assert "LOGFIRE_API_KEY" in text and "hosted datasets" in text


def test_readme_documents_safe_experiment_and_trace_capture_flow() -> None:
    text = README.read_text()

    assert "--kind model --smoke" in text
    assert "--kind prompt --smoke" in text
    assert "--full" in text
    assert "completed smoke" in text.lower()
    assert "Live View" in text
    assert "database-plus" in text
    assert "Review the extracted input, expected output, and metadata" in text
    assert "source trace" in text.lower()
