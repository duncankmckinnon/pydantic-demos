import type { Annotation, Annotator, InteractionsPage, Project, ProjectSummary } from "./types";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`${options?.method ?? "GET"} ${path} failed: ${response.status} ${body}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function listProjects(): Promise<ProjectSummary[]> {
  return request<ProjectSummary[]>("/api/projects");
}

export function getProject(projectId: number): Promise<Project> {
  return request<Project>(`/api/projects/${projectId}`);
}

export function updateProject(
  projectId: number,
  payload: {
    criteria_text?: string;
    top_level_agent_name?: string;
    labels?: { id: number | null; name: string }[];
  },
): Promise<Project> {
  return request<Project>(`/api/projects/${projectId}`, { method: "PUT", body: JSON.stringify(payload) });
}

export function listAnnotators(): Promise<Annotator[]> {
  return request<Annotator[]>("/api/annotators");
}

export function createAnnotator(name: string): Promise<Annotator> {
  return request<Annotator>("/api/annotators", { method: "POST", body: JSON.stringify({ name }) });
}

export function renameAnnotator(id: number, name: string): Promise<Annotator> {
  return request<Annotator>(`/api/annotators/${id}`, { method: "PUT", body: JSON.stringify({ name }) });
}

export function deleteAnnotator(id: number): Promise<void> {
  return request<void>(`/api/annotators/${id}`, { method: "DELETE" });
}

export function listInteractions(
  projectId: number,
  annotatorId: number,
  cursor: string | null,
): Promise<InteractionsPage> {
  const params = new URLSearchParams({ annotator_id: String(annotatorId) });
  if (cursor) params.set("cursor", cursor);
  return request<InteractionsPage>(`/api/projects/${projectId}/interactions?${params.toString()}`);
}

export function upsertAnnotation(
  projectId: number,
  traceId: string,
  spanId: string,
  payload: { annotator_id: number; label_id: number | null; description: string },
): Promise<Annotation> {
  return request<Annotation>(`/api/projects/${projectId}/annotations/${traceId}/${spanId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}
