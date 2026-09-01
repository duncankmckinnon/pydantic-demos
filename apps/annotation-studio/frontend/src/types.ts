export interface Label {
  id: number;
  name: string;
  sort_order: number;
}

export interface ProjectSummary {
  id: number;
  name: string;
  created_at: string;
  updated_at: string;
}

export type Project = ProjectSummary;

export interface QueueSummary {
  id: number;
  project_id: number;
  name: string;
  query: string;
  criteria_text: string;
  sampling_percentage: number;
  last_refreshed_at: string | null;
  labels: Label[];
  annotator_ids: number[];
  item_count: number;
  created_at: string;
  updated_at: string;
}

export interface Queue extends Omit<QueueSummary, "item_count"> {
  is_accessible: boolean;
}

export interface Annotator {
  id: number;
  name: string;
  created_at: string;
  updated_at: string;
}

export interface Annotation {
  id: number;
  label_id: number | null;
  description: string;
  annotator_id: number;
  revision: number;
  writeback_status: "pending" | "written" | "failed";
  writeback_error: string | null;
  written_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface MessagePart {
  type: string;
  content?: string;
  id?: string;
  name?: string;
  arguments?: unknown;
  result?: unknown;
}

export interface Message {
  role: string;
  parts: MessagePart[];
  finish_reason?: string | null;
}

export interface QueueItem {
  trace_id: string;
  span_id: string;
  start_timestamp: string;
  input_text?: string;
  output_text?: string;
  full_conversation?: Message[];
  trace_url?: string;
  raw_row: Record<string, unknown> | null;
  unavailable: boolean;
  annotation: Annotation | null;
}

export interface QueueItemsPage {
  items: QueueItem[];
  next_cursor: string | null;
}

export interface DatasetResult {
  name: string;
  case_count: number;
  skipped_count: number;
}
