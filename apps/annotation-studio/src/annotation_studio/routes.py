import sqlite3
from dataclasses import asdict

from anyio import to_thread
from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from annotation_studio import db
from annotation_studio.logfire_client import fetch_project_interactions, validate_agent_name
from annotation_studio.logfire_writer import AnnotationWriter
from annotation_studio.settings import AppSettings, SourceSettings

PAGE_SIZE = 20


class LabelPayload(BaseModel):
    id: int | None = None
    name: str


class ProjectUpdateRequest(BaseModel):
    criteria_text: str | None = None
    top_level_agent_name: str | None = None
    labels: list[LabelPayload] | None = None


class AnnotatorRequest(BaseModel):
    name: str


class AnnotationUpdateRequest(BaseModel):
    annotator_id: int
    label_id: int | None = None
    description: str = ""


def register_routes(
    app: FastAPI,
    conn: sqlite3.Connection,
    source_settings: SourceSettings,
    app_settings: AppSettings,
    writer: AnnotationWriter,
) -> None:
    router = APIRouter(prefix="/api")

    @router.get("/projects")
    async def list_projects() -> list[dict]:
        return db.list_projects(conn)

    @router.get("/projects/{project_id}")
    async def get_project(project_id: int) -> dict:
        project = db.get_project(conn, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project_not_found")
        project["labels"] = db.list_labels(conn, project_id)
        return project

    @router.put("/projects/{project_id}")
    async def update_project(project_id: int, payload: ProjectUpdateRequest) -> dict:
        if db.get_project(conn, project_id) is None:
            raise HTTPException(status_code=404, detail="project_not_found")
        labels = (
            [db.LabelInput(id=label.id, name=label.name) for label in payload.labels]
            if payload.labels is not None
            else None
        )
        try:
            return db.update_project(
                conn, project_id, payload.criteria_text, payload.top_level_agent_name, labels
            )
        except db.ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except db.ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.get("/annotators")
    async def list_annotators() -> list[dict]:
        return db.list_annotators(conn)

    @router.post("/annotators")
    async def create_annotator(payload: AnnotatorRequest) -> dict:
        try:
            return db.create_annotator(conn, payload.name)
        except db.ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except db.ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.put("/annotators/{annotator_id}")
    async def rename_annotator(annotator_id: int, payload: AnnotatorRequest) -> dict:
        if db.get_annotator(conn, annotator_id) is None:
            raise HTTPException(status_code=404, detail="annotator_not_found")
        try:
            return db.rename_annotator(conn, annotator_id, payload.name)
        except db.ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except db.ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.delete("/annotators/{annotator_id}", status_code=204)
    async def delete_annotator(annotator_id: int) -> None:
        if db.get_annotator(conn, annotator_id) is None:
            raise HTTPException(status_code=404, detail="annotator_not_found")
        try:
            db.delete_annotator(conn, annotator_id)
        except db.ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.get("/projects/{project_id}/interactions")
    async def list_interactions(
        project_id: int, annotator_id: int | None = None, cursor: str | None = None
    ) -> dict:
        # annotator_id is optional at the FastAPI level (not `annotator_id: int` with no
        # default) so a missing value reaches this explicit check and returns HTTP 400 with
        # the same {"detail": ...} error shape every other validation failure in this API
        # uses — a required-but-typed query param would instead fail FastAPI's own request
        # validation with a 422 and a different error body.
        project = db.get_project(conn, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project_not_found")
        if annotator_id is None:
            raise HTTPException(status_code=400, detail="annotator_id is required")
        if db.get_annotator(conn, annotator_id) is None:
            raise HTTPException(status_code=400, detail="unknown_annotator_id")

        try:
            interactions, next_cursor = await fetch_project_interactions(
                source_settings.read_token, project["top_level_agent_name"], cursor, PAGE_SIZE
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        items = []
        for interaction in interactions:
            annotation = db.get_annotation(conn, project_id, interaction.trace_id, interaction.span_id, annotator_id)
            items.append({**asdict(interaction), "annotation": annotation})

        return {"items": items, "next_cursor": next_cursor}

    @router.put("/projects/{project_id}/annotations/{trace_id}/{span_id}")
    async def upsert_annotation(
        project_id: int, trace_id: str, span_id: str, payload: AnnotationUpdateRequest
    ) -> dict:
        if db.get_project(conn, project_id) is None:
            raise HTTPException(status_code=404, detail="project_not_found")
        try:
            annotation = db.upsert_annotation(
                conn, project_id, trace_id, span_id, payload.annotator_id, payload.label_id, payload.description,
            )
        except db.ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        annotator = db.get_annotator(conn, payload.annotator_id)
        label = db.get_label(conn, payload.label_id) if payload.label_id else None
        try:
            # force_flush() blocks for up to 3s — run off the event loop so one slow
            # write-back can't stall every other concurrent request.
            await to_thread.run_sync(writer.write, annotation, annotator, label)
        except Exception as exc:
            db.mark_writeback_failed(conn, annotation["id"], annotation["revision"], f"{type(exc).__name__}: {exc}")
        else:
            db.mark_writeback_written(conn, annotation["id"], annotation["revision"])
        return db.get_annotation(conn, project_id, trace_id, span_id, payload.annotator_id)

    app.include_router(router)
