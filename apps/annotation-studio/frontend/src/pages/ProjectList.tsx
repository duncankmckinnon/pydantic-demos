import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { listProjects } from "../api";
import { useAnnotator } from "../annotator";
import type { ProjectSummary } from "../types";

export function ProjectList() {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { annotators, selectedId } = useAnnotator();
  const selectedName = annotators.find((a) => a.id === selectedId)?.name;

  useEffect(() => {
    listProjects()
      .then(setProjects)
      .catch((err: unknown) => setError(String(err)));
  }, []);

  if (error) return <p className="error">{error}</p>;
  if (projects === null) return <p>Loading…</p>;

  return (
    <div className="project-list">
      <header className="app-header">
        <h1>Annotation Studio</h1>
        <Link to="/annotators">{selectedName ?? "Choose annotator"}</Link>
      </header>
      {projects.map((project) => (
        <Link key={project.id} to={`/projects/${project.id}`} className="project-card">
          <h2>{project.name}</h2>
          <p>Source agent: {project.top_level_agent_name}</p>
        </Link>
      ))}
    </div>
  );
}
