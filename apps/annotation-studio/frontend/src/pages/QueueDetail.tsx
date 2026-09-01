import { useCallback, useEffect, useRef, useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";

import { createDataset, getQueue, listQueueItems, refreshQueue } from "../api";
import { useAnnotator } from "../annotator";
import { AppHeader } from "../components/AppHeader";
import { QueueItemRow } from "../components/QueueItemRow";
import type { Queue, QueueItem } from "../types";

export function QueueDetail() {
  const { queueId: queueIdParam } = useParams<{ queueId: string }>();
  const queueId = Number(queueIdParam);
  const { selectedId } = useAnnotator();

  const [queue, setQueue] = useState<Queue | null>(null);
  const [items, setItems] = useState<QueueItem[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshMessage, setRefreshMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showDatasetForm, setShowDatasetForm] = useState(false);
  const [datasetName, setDatasetName] = useState("");
  const [datasetLabelId, setDatasetLabelId] = useState<number | "">("");
  const [datasetResult, setDatasetResult] = useState<string | null>(null);
  const [datasetSaving, setDatasetSaving] = useState(false);

  const activeAnnotatorIdRef = useRef(selectedId);
  useEffect(() => {
    activeAnnotatorIdRef.current = selectedId;
  }, [selectedId]);

  const loadQueue = useCallback(() => {
    getQueue(queueId, selectedId)
      .then(setQueue)
      .catch((err: unknown) => setError(String(err)));
  }, [queueId, selectedId]);

  const loadItems = useCallback(
    (cursor: string | null) => {
      if (selectedId === null) return;
      const firedForAnnotatorId = selectedId;
      setLoading(true);
      listQueueItems(queueId, selectedId, cursor)
        .then((page) => {
          if (activeAnnotatorIdRef.current !== firedForAnnotatorId) return;
          setItems((prev) => (cursor ? [...prev, ...page.items] : page.items));
          setNextCursor(page.next_cursor);
        })
        .catch((err: unknown) => setError(String(err)))
        .finally(() => {
          if (activeAnnotatorIdRef.current === firedForAnnotatorId) setLoading(false);
        });
    },
    [queueId, selectedId],
  );

  useEffect(loadQueue, [loadQueue]);
  useEffect(() => {
    setItems([]);
    setNextCursor(null);
    loadItems(null);
  }, [queueId, selectedId]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleRefresh = async () => {
    setRefreshing(true);
    setRefreshMessage(null);
    try {
      const result = await refreshQueue(queueId, selectedId);
      setRefreshMessage(`Pulled ${result.new_item_count} new item(s) — ${result.total_item_count} total.`);
      setItems([]);
      setNextCursor(null);
      loadItems(null);
    } catch (err) {
      setError(String(err));
    } finally {
      setRefreshing(false);
    }
  };

  const handleCreateDataset = async () => {
    setDatasetSaving(true);
    setDatasetResult(null);
    try {
      const result = await createDataset(queueId, datasetName, datasetLabelId === "" ? null : datasetLabelId);
      setDatasetResult(`Pushed "${result.name}": ${result.case_count} case(s), ${result.skipped_count} skipped.`);
    } catch (err) {
      setError(String(err));
    } finally {
      setDatasetSaving(false);
    }
  };

  if (selectedId === null) return <Navigate to="/annotators" replace />;
  if (error)
    return (
      <div className="page">
        <AppHeader />
        <main className="page-body">
          <p className="error-banner">{error}</p>
        </main>
      </div>
    );
  if (queue === null)
    return (
      <div className="page">
        <AppHeader />
        <main className="page-body">
          <p className="loading-text">Loading…</p>
        </main>
      </div>
    );
  if (!queue.is_accessible)
    return (
      <div className="page">
        <AppHeader />
        <main className="page-body">
          <p className="error-banner">You're not assigned to this queue.</p>
        </main>
      </div>
    );

  const exploreLink = items.find((item) => item.trace_url)?.trace_url;
  const exploreBase = exploreLink ? exploreLink.split("?")[0] + "/explore" : null;

  return (
    <div className="page page-wide">
      <AppHeader />
      <main className="page-body">
        <div className="page-heading page-heading-row">
          <div>
            <h1>{queue.name}</h1>
            {queue.criteria_text && <p className="queue-criteria">{queue.criteria_text}</p>}
          </div>
          <div className="queue-detail-actions">
            <Link className="btn btn-secondary" to={`/queues/${queue.id}/edit`}>
              Edit
            </Link>
            <button className="btn btn-secondary" onClick={handleRefresh} disabled={refreshing}>
              {refreshing ? "Refreshing…" : "Refresh"}
            </button>
            {exploreBase && (
              <a
                className="btn-link"
                href={`${exploreBase}?q=${encodeURIComponent(queue.query)}`}
                target="_blank"
                rel="noopener noreferrer"
              >
                Open in Logfire Explore ↗
              </a>
            )}
            <button className="btn btn-primary" onClick={() => setShowDatasetForm((v) => !v)}>
              Create dataset
            </button>
          </div>
        </div>
        {refreshMessage && <p className="status-message status-message-success">{refreshMessage}</p>}

        {showDatasetForm && (
          <section className="card dataset-form">
            <div className="field">
              <label htmlFor="dataset-name">Dataset name</label>
              <input id="dataset-name" value={datasetName} onChange={(e) => setDatasetName(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="dataset-label">Only include this label (optional)</label>
              <select
                id="dataset-label"
                value={datasetLabelId}
                onChange={(e) => setDatasetLabelId(e.target.value === "" ? "" : Number(e.target.value))}
              >
                <option value="">All annotated items</option>
                {queue.labels.map((label) => (
                  <option key={label.id} value={label.id}>
                    {label.name}
                  </option>
                ))}
              </select>
            </div>
            <button className="btn btn-primary" onClick={handleCreateDataset} disabled={datasetSaving || !datasetName}>
              {datasetSaving ? "Pushing…" : "Push to Logfire"}
            </button>
            {datasetResult && <p className="status-message status-message-success">{datasetResult}</p>}
          </section>
        )}

        <div className="interaction-list">
          {items.map((item) => (
            <QueueItemRow
              key={`${item.trace_id}:${item.span_id}`}
              queueId={queue.id}
              annotatorId={selectedId}
              item={item}
              labels={queue.labels}
            />
          ))}
        </div>
        {nextCursor && (
          <button className="btn btn-secondary load-more-btn" onClick={() => loadItems(nextCursor)} disabled={loading}>
            {loading ? "Loading…" : "Load more"}
          </button>
        )}
      </main>
    </div>
  );
}
