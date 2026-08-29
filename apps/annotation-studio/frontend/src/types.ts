export interface Label {
  id: number;
  name: string;
  sort_order: number;
}

export interface ProjectSummary {
  id: number;
  name: string;
  top_level_agent_name: string;
  criteria_text: string;
  created_at: string;
  updated_at: string;
}

export interface Project extends ProjectSummary {
  labels: Label[];
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

export interface Interaction {
  trace_id: string;
  span_id: string;
  start_timestamp: string;
  input_text: string;
  output_text: string;
  full_conversation: Message[];
  trace_url: string;
  raw_attributes: Record<string, unknown> | null;
  annotation: Annotation | null;
}

export interface InteractionsPage {
  items: Interaction[];
  next_cursor: string | null;
}
