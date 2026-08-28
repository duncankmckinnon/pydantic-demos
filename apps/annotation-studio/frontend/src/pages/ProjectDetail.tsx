import { useCallback, useEffect, useRef, useState } from "react";
import { Navigate, useParams } from "react-router-dom";

import { getProject, listInteractions, updateProject } from "../api";
import { useAnnotator } from "../annotator";
import { AppHeader } from "../components/AppHeader";
import { InteractionRow } from "../components/InteractionRow";
import { ProjectEditor } from "../components/ProjectEditor";
import type { Interaction, Project } from "../types";

export function ProjectDetail() {
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);
  const { selectedId } = useAnnotator();

  const [project, setProject] = useState<Project | null>(null);
  const [interactions, setInteractions] = useState<Interaction[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Tracks which annotator is currently "owned" by the UI, so a slow "Load more"
  // response fired for a previously-selected annotator can't clobber a newer
  // fetch's results after the user switches annotators.
  const activeAnnotatorIdRef = useRef(selectedId);
  useEffect(() => {
    activeAnnotatorIdRef.current = selectedId;
  }, [selectedId]);

  const loadProject = useCallback(() => {
    getProject(projectId)
      .then(setProject)
      .catch((err: unknown) => setError(String(err)));
  }, [projectId]);

  const loadInteractions = useCallback(
    (cursor: string | null) => {
      if (selectedId === null) return;
      const firedForAnnotatorId = selectedId;
      setLoading(true);
      listInteractions(projectId, selectedId, cursor)
        .then((page) => {
          // Discard stale responses: if the active annotator changed while this
          // request was in flight, these results belong to a page the user is no
          // longer viewing and must not be merged into the current list.
          if (activeAnnotatorIdRef.current !== firedForAnnotatorId) return;
          setInteractions((prev) => (cursor ? [...prev, ...page.items] : page.items));
          setNextCursor(page.next_cursor);
        })
        .catch((err: unknown) => setError(String(err)))
        .finally(() => {
          if (activeAnnotatorIdRef.current === firedForAnnotatorId) setLoading(false);
        });
    },
    [projectId, selectedId],
  );

  useEffect(() => {
    loadProject();
  }, [loadProject]);

  useEffect(() => {
    // Re-fetch from page 1 whenever the project or the selected annotator changes — a
    // different annotator has different existing grades merged into each interaction.
    setInteractions([]);
    setNextCursor(null);
    loadInteractions(null);
  }, [projectId, selectedId]); // eslint-disable-line react-hooks/exhaustive-deps

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
  if (project === null)
    return (
      <div className="page">
        <AppHeader />
        <main className="page-body">
          <p className="loading-text">Loading…</p>
        </main>
      </div>
    );

  return (
    <div className="page page-wide">
      <AppHeader />
      <main className="page-body">
        <div className="page-heading">
          <h1>{project.name}</h1>
        </div>
        <div className="project-detail-layout">
          <div className="project-detail-main">
            <h2 className="interactions-heading">Interactions</h2>
            <div className="interaction-list">
              {interactions.map((interaction) => (
                <InteractionRow
                  key={`${interaction.trace_id}:${interaction.span_id}`}
                  projectId={projectId}
                  annotatorId={selectedId}
                  interaction={interaction}
                  labels={project.labels}
                />
              ))}
            </div>
            {nextCursor && (
              <button
                className="btn btn-secondary load-more-btn"
                onClick={() => loadInteractions(nextCursor)}
                disabled={loading}
              >
                {loading ? "Loading…" : "Load more"}
              </button>
            )}
          </div>

          <aside className="project-detail-sidebar">
            <ProjectEditor
              key={project.updated_at}
              initialCriteriaText={project.criteria_text}
              initialAgentName={project.top_level_agent_name}
              initialLabels={project.labels}
              onSave={async (values) => {
                const updated = await updateProject(projectId, values);
                setProject(updated);
              }}
            />
          </aside>
        </div>
      </main>
    </div>
  );
}
