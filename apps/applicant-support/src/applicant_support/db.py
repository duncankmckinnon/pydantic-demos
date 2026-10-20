from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class ApplicationRecord:
    application_id: str
    candidate_name: str
    candidate_email: str
    role_title: str
    status: str
    status_detail: str
    interview_starts_at: datetime | None


@dataclass(frozen=True)
class InterviewRecord:
    application_id: str
    starts_at: datetime


@dataclass(frozen=True)
class RoleRecord:
    role_id: str
    title: str
    location: str
    is_open: bool


class ApplicantDatabase:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS candidates (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE
                );

                CREATE TABLE IF NOT EXISTS roles (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    location TEXT NOT NULL,
                    is_open INTEGER NOT NULL CHECK (is_open IN (0, 1))
                );

                CREATE TABLE IF NOT EXISTS applications (
                    id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL REFERENCES candidates(id),
                    role_id TEXT NOT NULL REFERENCES roles(id),
                    status TEXT NOT NULL,
                    status_detail TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS interviews (
                    application_id TEXT PRIMARY KEY REFERENCES applications(id),
                    starts_at TEXT NOT NULL
                );
                """
            )
            connection.executemany(
                "INSERT OR IGNORE INTO candidates (id, name, email) VALUES (?, ?, ?)",
                [
                    ("CAND-001", "Maya Chen", "maya.chen@example.com"),
                    ("CAND-002", "Jordan Lee", "jordan.lee@example.com"),
                    ("CAND-003", "Sam Rivera", "sam.rivera@example.com"),
                ],
            )
            connection.executemany(
                "INSERT OR IGNORE INTO roles (id, title, location, is_open) VALUES (?, ?, ?, ?)",
                [
                    ("ROLE-001", "Product Engineer", "San Francisco, CA", 1),
                    ("ROLE-002", "Product Designer", "Remote (US)", 0),
                    ("ROLE-003", "Customer Support Specialist", "Austin, TX", 1),
                ],
            )
            connection.executemany(
                """
                INSERT OR IGNORE INTO applications
                    (id, candidate_id, role_id, status, status_detail)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        "APP-1001",
                        "CAND-001",
                        "ROLE-001",
                        "screening_passed",
                        "Passed initial screening; recruiter call scheduled.",
                    ),
                    (
                        "APP-1002",
                        "CAND-002",
                        "ROLE-002",
                        "screening_pending",
                        "Initial screening is still under review.",
                    ),
                    (
                        "APP-1003",
                        "CAND-003",
                        "ROLE-003",
                        "screening_not_selected",
                        "Not selected after initial screening.",
                    ),
                ],
            )
            connection.execute(
                "INSERT OR IGNORE INTO interviews (application_id, starts_at) VALUES (?, ?)",
                ("APP-1001", "2026-10-23T10:00:00-07:00"),
            )

    def get_application(self, application_id: str) -> ApplicationRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    a.id AS application_id,
                    c.name AS candidate_name,
                    c.email AS candidate_email,
                    r.title AS role_title,
                    a.status,
                    a.status_detail,
                    i.starts_at AS interview_starts_at
                FROM applications AS a
                JOIN candidates AS c ON c.id = a.candidate_id
                JOIN roles AS r ON r.id = a.role_id
                LEFT JOIN interviews AS i ON i.application_id = a.id
                WHERE a.id = ?
                """,
                (application_id,),
            ).fetchone()
        if row is None:
            return None
        starts_at = row["interview_starts_at"]
        return ApplicationRecord(
            application_id=row["application_id"],
            candidate_name=row["candidate_name"],
            candidate_email=row["candidate_email"],
            role_title=row["role_title"],
            status=row["status"],
            status_detail=row["status_detail"],
            interview_starts_at=datetime.fromisoformat(starts_at) if starts_at else None,
        )

    def reschedule_interview(
        self, application_id: str, starts_at: datetime
    ) -> InterviewRecord | None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE interviews SET starts_at = ? WHERE application_id = ?",
                (starts_at.isoformat(), application_id),
            )
        if cursor.rowcount == 0:
            return None
        return InterviewRecord(application_id=application_id, starts_at=starts_at)

    def search_roles(self, query: str) -> list[RoleRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, title, location, is_open
                FROM roles
                WHERE title LIKE ? COLLATE NOCASE
                ORDER BY title
                """,
                (f"%{query.strip()}%",),
            ).fetchall()
        return [
            RoleRecord(
                role_id=row["id"],
                title=row["title"],
                location=row["location"],
                is_open=bool(row["is_open"]),
            )
            for row in rows
        ]
