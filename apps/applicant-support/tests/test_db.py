from datetime import datetime

from applicant_support.db import ApplicantDatabase


def test_initialize_is_idempotent_and_seeds_known_application(tmp_path) -> None:
    database = ApplicantDatabase(tmp_path / "applicants.sqlite3")

    database.initialize()
    database.initialize()

    application = database.get_application("APP-1001")
    assert application is not None
    assert application.candidate_name == "Maya Chen"
    assert application.candidate_email == "maya.chen@example.com"
    assert application.role_title == "Product Engineer"
    assert application.status == "screening_passed"
    assert application.interview_starts_at == datetime.fromisoformat(
        "2026-10-23T10:00:00-07:00"
    )


def test_get_application_does_not_fall_back_for_unknown_id(tmp_path) -> None:
    database = ApplicantDatabase(tmp_path / "applicants.sqlite3")
    database.initialize()

    assert database.get_application("APP-DOES-NOT-EXIST") is None


def test_reschedule_interview_persists_new_time(tmp_path) -> None:
    database = ApplicantDatabase(tmp_path / "applicants.sqlite3")
    database.initialize()
    new_time = datetime.fromisoformat("2026-10-27T14:30:00-07:00")

    interview = database.reschedule_interview("APP-1001", new_time)

    assert interview is not None
    assert interview.application_id == "APP-1001"
    assert interview.starts_at == new_time
    assert database.get_application("APP-1001").interview_starts_at == new_time


def test_reschedule_does_not_create_missing_interview(tmp_path) -> None:
    database = ApplicantDatabase(tmp_path / "applicants.sqlite3")
    database.initialize()

    result = database.reschedule_interview(
        "APP-1002", datetime.fromisoformat("2026-10-29T09:00:00-07:00")
    )

    assert result is None
    assert database.get_application("APP-1002").interview_starts_at is None


def test_search_roles_returns_open_and_closed_matches(tmp_path) -> None:
    database = ApplicantDatabase(tmp_path / "applicants.sqlite3")
    database.initialize()

    open_matches = database.search_roles("engineer")
    closed_matches = database.search_roles("designer")

    assert [(role.title, role.is_open) for role in open_matches] == [
        ("Product Engineer", True)
    ]
    assert [(role.title, role.is_open) for role in closed_matches] == [
        ("Product Designer", False)
    ]
