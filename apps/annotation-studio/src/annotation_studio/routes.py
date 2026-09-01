import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from anyio import to_thread
from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from annotation_studio import db
from annotation_studio.logfire_client import (
    LOOKBACK_DAYS,
    fetch_queue_item_content,
    fetch_queue_matches,
    sample_included,
    validate_query,
    validate_query_columns,
)
from annotation_studio.logfire_datasets import push_queue_dataset
from annotation_studio.logfire_writer import AnnotationWriter
from annotation_studio.settings import AppSettings, SourceSettings

PAGE_SIZE = 20


class LabelPayload(BaseModel):
    id: int | None = None
    name: str


class ProjectUpdateRequest(BaseModel):
    name: str


class QueueCreateRequest(BaseModel):
    name: str
    query: str
    criteria_text: str = ""
    sampling_percentage: int = 100
    labels: list[LabelPayload]
    annotator_ids: list[int] = []


class QueueUpdateRequest(BaseModel):
    name: str | None = None
    query: str | None = None
    criteria_text: str | None = None
    sampling_percentage: int | None = None
    labels: list[LabelPayload] | None = None
    annotator_ids: list[int] | None = None


class AnnotatorRequest(BaseModel):
    name: str


class AnnotationUpdateRequest(BaseModel):
    annotator_id: int
    label_id: int | None = None
    description: str = ""


class DatasetCreateRequest(BaseModel):
    name: str
    label_id: int | None = None


def _queue_is_accessible(queue: dict, annotator_id: int | None) -> bool:
    if not queue["annotator_ids"]:
        return True
    return annotator_id is not None and annotator_id in queue["annotator_ids"]


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
        return project

    @router.put("/projects/{project_id}")
    async def update_project(project_id: int, payload: ProjectUpdateRequest) -> dict:
        if db.get_project(conn, project_id) is None:
            raise HTTPException(status_code=404, detail="project_not_found")
        try:
            return db.update_project(conn, project_id, payload.name)
        except db.ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.get("/projects/{project_id}/queues")
    async def list_queues(project_id: int, annotator_id: int | None = None) -> list[dict]:
        if db.get_project(conn, project_id) is None:
            raise HTTPException(status_code=404, detail="project_not_found")
        queues = db.list_queues(conn, project_id)
        for queue in queues:
            queue["is_accessible"] = _queue_is_accessible(queue, annotator_id)
        return queues

    @router.post("/projects/{project_id}/queues")
    async def create_queue(project_id: int, payload: QueueCreateRequest) -> dict:
        if db.get_project(conn, project_id) is None:
            raise HTTPException(status_code=404, detail="project_not_found")
        try:
            query = validate_query(payload.query)
            await validate_query_columns(source_settings.read_token, query)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        labels = [db.LabelInput(id=label.id, name=label.name) for label in payload.labels]
        try:
            return db.create_queue(
                conn, project_id, payload.name, query, payload.criteria_text,
                payload.sampling_percentage, labels, payload.annotator_ids,
            )
        except db.ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except db.ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.get("/queues/{queue_id}")
    async def get_queue(queue_id: int, annotator_id: int | None = None) -> dict:
        queue = db.get_queue(conn, queue_id)
        if queue is None:
            raise HTTPException(status_code=404, detail="queue_not_found")
        queue["is_accessible"] = _queue_is_accessible(queue, annotator_id)
        return queue

    @router.put("/queues/{queue_id}")
    async def update_queue(queue_id: int, payload: QueueUpdateRequest) -> dict:
        if db.get_queue(conn, queue_id) is None:
            raise HTTPException(status_code=404, detail="queue_not_found")
        query = None
        if payload.query is not None:
            try:
                query = validate_query(payload.query)
                await validate_query_columns(source_settings.read_token, query)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
        labels = (
            [db.LabelInput(id=label.id, name=label.name) for label in payload.labels]
            if payload.labels is not None else None
        )
        try:
            return db.update_queue(
                conn, queue_id, payload.name, query, payload.criteria_text,
                payload.sampling_percentage, labels, payload.annotator_ids,
            )
        except db.ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except db.ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.delete("/queues/{queue_id}", status_code=204)
    async def delete_queue(queue_id: int) -> None:
        if db.get_queue(conn, queue_id) is None:
            raise HTTPException(status_code=404, detail="queue_not_found")
        db.delete_queue(conn, queue_id)

    @router.post("/queues/{queue_id}/refresh")
    async def refresh_queue(queue_id: int, annotator_id: int | None = None) -> dict:
        queue = db.get_queue(conn, queue_id)
        if queue is None:
            raise HTTPException(status_code=404, detail="queue_not_found")
        if not _queue_is_accessible(queue, annotator_id):
            raise HTTPException(status_code=403, detail="not_assigned_to_queue")

        now = datetime.now(timezone.utc)
        # Always scans the full lookback window rather than incrementally since
        # last_refreshed_at — an earlier refresh (e.g. against a query that was broken or
        # matched nothing at the time) must never permanently narrow later refreshes' search
        # window once the query is fixed. last_refreshed_at is still recorded, purely as
        # informational bookkeeping.
        min_timestamp = now - timedelta(days=LOOKBACK_DAYS)
        try:
            matches = await fetch_queue_matches(source_settings.read_token, queue["query"], min_timestamp, now, limit=1000)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Logfire query failed: {exc}")

        sampled_in = [
            match for match in matches
            if sample_included(queue_id, match["trace_id"], match["span_id"], queue["sampling_percentage"])
        ]
        new_item_count = db.insert_queue_items(conn, queue_id, sampled_in)
        db.set_queue_last_refreshed(conn, queue_id, now.isoformat())
        total_item_count = len(db.list_queue_items(conn, queue_id, None, 10_000_000)[0])
        return {"new_item_count": new_item_count, "total_item_count": total_item_count}

    @router.get("/queues/{queue_id}/items")
    async def list_items(queue_id: int, annotator_id: int | None = None, cursor: str | None = None) -> dict:
        queue = db.get_queue(conn, queue_id)
        if queue is None:
            raise HTTPException(status_code=404, detail="queue_not_found")
        if annotator_id is None:
            raise HTTPException(status_code=400, detail="annotator_id is required")
        if db.get_annotator(conn, annotator_id) is None:
            raise HTTPException(status_code=400, detail="unknown_annotator_id")
        if not _queue_is_accessible(queue, annotator_id):
            raise HTTPException(status_code=403, detail="not_assigned_to_queue")

        page, next_cursor = db.list_queue_items(conn, queue_id, cursor, PAGE_SIZE)
        content = await fetch_queue_item_content(
            source_settings.read_token, [(item["trace_id"], item["span_id"]) for item in page]
        )

        items = []
        for item in page:
            interaction = content.get((item["trace_id"], item["span_id"]))
            annotation = db.get_annotation(conn, queue_id, item["trace_id"], item["span_id"], annotator_id)
            if interaction is None:
                items.append({
                    "trace_id": item["trace_id"], "span_id": item["span_id"],
                    "start_timestamp": item["start_timestamp"], "unavailable": True,
                    "annotation": annotation,
                })
            else:
                items.append({**asdict(interaction), "unavailable": False, "annotation": annotation})
        return {"items": items, "next_cursor": next_cursor}

    @router.put("/queues/{queue_id}/annotations/{trace_id}/{span_id}")
    async def upsert_annotation(queue_id: int, trace_id: str, span_id: str, payload: AnnotationUpdateRequest) -> dict:
        queue = db.get_queue(conn, queue_id)
        if queue is None:
            raise HTTPException(status_code=404, detail="queue_not_found")
        if not _queue_is_accessible(queue, payload.annotator_id):
            raise HTTPException(status_code=403, detail="not_assigned_to_queue")
        try:
            annotation = db.upsert_annotation(
                conn, queue_id, trace_id, span_id, payload.annotator_id, payload.label_id, payload.description,
            )
        except db.ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        annotator = db.get_annotator(conn, payload.annotator_id)
        label = db.get_label(conn, payload.label_id) if payload.label_id else None
        try:
            await to_thread.run_sync(writer.write, annotation, annotator, label)
        except Exception as exc:
            db.mark_writeback_failed(conn, annotation["id"], annotation["revision"], f"{type(exc).__name__}: {exc}")
        else:
            db.mark_writeback_written(conn, annotation["id"], annotation["revision"])
        return db.get_annotation(conn, queue_id, trace_id, span_id, payload.annotator_id)

    @router.post("/queues/{queue_id}/datasets")
    async def create_dataset(queue_id: int, payload: DatasetCreateRequest) -> dict:
        queue = db.get_queue(conn, queue_id)
        if queue is None:
            raise HTTPException(status_code=404, detail="queue_not_found")
        annotations = db.list_annotations_for_dataset(conn, queue_id, payload.label_id)
        label_lookup = {label["id"]: label["name"] for label in queue["labels"]}
        annotator_lookup = {a["id"]: a["name"] for a in db.list_annotators(conn)}
        return await push_queue_dataset(
            source_settings.read_token, source_settings.datasets_token, payload.name,
            annotations, label_lookup, annotator_lookup,
        )

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

    app.include_router(router)
