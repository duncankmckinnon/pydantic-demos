import type {
  Annotation,
  Annotator,
  DatasetResult,
  Project,
  ProjectSummary,
  Queue,
  QueueItemsPage,
  QueueSummary,
} from "./types";

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

export function updateProject(projectId: number, name: string): Promise<Project> {
  return request<Project>(`/api/projects/${projectId}`, { method: "PUT", body: JSON.stringify({ name }) });
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

export interface QueueDraft {
  name: string;
  query: string;
  criteria_text: string;
  sampling_percentage: number;
  labels: { id: number | null; name: string }[];
  annotator_ids: number[];
}

export function listQueues(projectId: number, annotatorId: number | null): Promise<QueueSummary[]> {
  const params = new URLSearchParams();
  if (annotatorId !== null) params.set("annotator_id", String(annotatorId));
  const qs = params.toString();
  return request<QueueSummary[]>(`/api/projects/${projectId}/queues${qs ? `?${qs}` : ""}`);
}

export function createQueue(projectId: number, draft: QueueDraft): Promise<Queue> {
  return request<Queue>(`/api/projects/${projectId}/queues`, { method: "POST", body: JSON.stringify(draft) });
}

export function getQueue(queueId: number, annotatorId: number | null): Promise<Queue> {
  const params = new URLSearchParams();
  if (annotatorId !== null) params.set("annotator_id", String(annotatorId));
  const qs = params.toString();
  return request<Queue>(`/api/queues/${queueId}${qs ? `?${qs}` : ""}`);
}

export function updateQueue(queueId: number, draft: Partial<QueueDraft>): Promise<Queue> {
  return request<Queue>(`/api/queues/${queueId}`, { method: "PUT", body: JSON.stringify(draft) });
}

export function deleteQueue(queueId: number): Promise<void> {
  return request<void>(`/api/queues/${queueId}`, { method: "DELETE" });
}

export function refreshQueue(
  queueId: number,
  annotatorId: number | null,
): Promise<{ new_item_count: number; total_item_count: number }> {
  const params = new URLSearchParams();
  if (annotatorId !== null) params.set("annotator_id", String(annotatorId));
  const qs = params.toString();
  return request(`/api/queues/${queueId}/refresh${qs ? `?${qs}` : ""}`, { method: "POST" });
}

export function clearAndRefreshQueue(
  queueId: number,
  annotatorId: number | null,
): Promise<{ new_item_count: number; total_item_count: number }> {
  const params = new URLSearchParams();
  if (annotatorId !== null) params.set("annotator_id", String(annotatorId));
  const qs = params.toString();
  return request(`/api/queues/${queueId}/clear${qs ? `?${qs}` : ""}`, { method: "POST" });
}

export function listQueueItems(queueId: number, annotatorId: number, cursor: string | null): Promise<QueueItemsPage> {
  const params = new URLSearchParams({ annotator_id: String(annotatorId) });
  if (cursor) params.set("cursor", cursor);
  return request<QueueItemsPage>(`/api/queues/${queueId}/items?${params.toString()}`);
}

export function upsertQueueAnnotation(
  queueId: number,
  traceId: string,
  spanId: string,
  payload: { annotator_id: number; label_id: number | null; description: string },
): Promise<Annotation> {
  return request<Annotation>(`/api/queues/${queueId}/annotations/${traceId}/${spanId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function createDataset(queueId: number, name: string, labelId: number | null): Promise<DatasetResult> {
  return request<DatasetResult>(`/api/queues/${queueId}/datasets`, {
    method: "POST",
    body: JSON.stringify({ name, label_id: labelId }),
  });
}
