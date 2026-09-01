import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { deleteQueue, getProject, listQueues } from "../api";
import { useAnnotator } from "../annotator";
import { AppHeader } from "../components/AppHeader";
import type { Project, QueueSummary } from "../types";

export function ProjectDetail() {
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);
  const { selectedId } = useAnnotator();

  const [project, setProject] = useState<Project | null>(null);
  const [queues, setQueues] = useState<QueueSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    getProject(projectId)
      .then(setProject)
      .catch((err: unknown) => setError(String(err)));
    listQueues(projectId, selectedId)
      .then(setQueues)
      .catch((err: unknown) => setError(String(err)));
  }, [projectId, selectedId]);

  useEffect(load, [load]);

  const handleDelete = async (queueId: number) => {
    if (!confirm("Delete this queue and all its annotations? This cannot be undone.")) return;
    await deleteQueue(queueId);
    load();
  };

  return (
    <div className="page page-wide">
      <AppHeader />
      <main className="page-body">
        {error && <p className="error-banner">{error}</p>}
        {project && (
          <div className="page-heading page-heading-row">
            <h1>{project.name}</h1>
            <Link className="btn btn-primary" to={`/projects/${projectId}/queues/new`}>
              + New queue
            </Link>
          </div>
        )}
        {queues !== null && (
          <div className="queue-list">
            {queues.map((queue) => (
              <div key={queue.id} className="card queue-card">
                <Link to={`/queues/${queue.id}`} className="queue-card-main">
                  <h2>{queue.name}</h2>
                  <p className="queue-card-meta">
                    {queue.item_count} item{queue.item_count === 1 ? "" : "s"} · {queue.sampling_percentage}% sampled ·{" "}
                    {queue.annotator_ids.length === 0
                      ? "open to all annotators"
                      : `${queue.annotator_ids.length} assigned annotator(s)`}
                  </p>
                </Link>
                <div className="queue-card-actions">
                  <Link className="btn btn-secondary" to={`/queues/${queue.id}/edit`}>
                    Edit
                  </Link>
                  <button className="btn btn-danger" onClick={() => handleDelete(queue.id)}>
                    Delete
                  </button>
                </div>
              </div>
            ))}
            {queues.length === 0 && <p className="loading-text">No queues yet — create one to get started.</p>}
          </div>
        )}
      </main>
    </div>
  );
}
