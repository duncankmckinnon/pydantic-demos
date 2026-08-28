import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { listProjects } from "../api";
import { AppHeader } from "../components/AppHeader";
import type { ProjectSummary } from "../types";

export function ProjectList() {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listProjects()
      .then(setProjects)
      .catch((err: unknown) => setError(String(err)));
  }, []);

  if (error) return <p className="error">{error}</p>;
  if (projects === null) return <p>Loading…</p>;

  return (
    <div className="project-list">
      <AppHeader />
      {projects.map((project) => (
        <Link key={project.id} to={`/projects/${project.id}`} className="project-card">
          <h2>{project.name}</h2>
          <p>Source agent: {project.top_level_agent_name}</p>
        </Link>
      ))}
    </div>
  );
}
