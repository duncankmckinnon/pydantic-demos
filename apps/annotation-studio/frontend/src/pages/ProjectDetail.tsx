import { useCallback, useEffect, useState } from "react";
import { Navigate, useParams } from "react-router-dom";

import { getProject, listInteractions, updateProject } from "../api";
import { useAnnotator } from "../annotator";
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

  const loadProject = useCallback(() => {
    getProject(projectId)
      .then(setProject)
      .catch((err: unknown) => setError(String(err)));
  }, [projectId]);

  const loadInteractions = useCallback(
    (cursor: string | null) => {
      if (selectedId === null) return;
      setLoading(true);
      listInteractions(projectId, selectedId, cursor)
        .then((page) => {
          setInteractions((prev) => (cursor ? [...prev, ...page.items] : page.items));
          setNextCursor(page.next_cursor);
        })
        .catch((err: unknown) => setError(String(err)))
        .finally(() => setLoading(false));
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
  if (error) return <p className="error">{error}</p>;
  if (project === null) return <p>Loading…</p>;

  return (
    <div className="project-detail">
      <h1>{project.name}</h1>
      <ProjectEditor
        initialCriteriaText={project.criteria_text}
        initialAgentName={project.top_level_agent_name}
        initialLabels={project.labels}
        onSave={async (values) => {
          const updated = await updateProject(projectId, values);
          setProject(updated);
        }}
      />

      <h2>Interactions</h2>
      {interactions.map((interaction) => (
        <InteractionRow
          key={`${interaction.trace_id}:${interaction.span_id}`}
          projectId={projectId}
          annotatorId={selectedId}
          interaction={interaction}
          labels={project.labels}
        />
      ))}
      {nextCursor && (
        <button onClick={() => loadInteractions(nextCursor)} disabled={loading}>
          {loading ? "Loading…" : "Load more"}
        </button>
      )}
    </div>
  );
}
