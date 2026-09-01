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

  return (
    <div className="page">
      <AppHeader />
      <main className="page-body">
        {error && <p className="error-banner">{error}</p>}
        {projects === null && !error && <p className="loading-text">Loading projects…</p>}
        {projects !== null && (
          <div className="project-grid">
            {projects.map((project) => (
              <Link key={project.id} to={`/projects/${project.id}`} className="project-card">
                <span className="project-card-icon" aria-hidden="true">
                  {project.name.slice(0, 1).toUpperCase()}
                </span>
                <div className="project-card-body">
                  <h2>{project.name}</h2>
                </div>
                <span className="project-card-arrow" aria-hidden="true">
                  →
                </span>
              </Link>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
