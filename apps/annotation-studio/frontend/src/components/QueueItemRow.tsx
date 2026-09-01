import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";

import { upsertQueueAnnotation } from "../api";
import type { Label, Message, MessagePart, QueueItem } from "../types";

interface Props {
  queueId: number;
  annotatorId: number;
  item: QueueItem;
  labels: Label[];
}

function renderPart(part: MessagePart, key: number) {
  if (part.type === "text") return <p key={key}>{part.content}</p>;
  if (part.type === "tool_call") {
    return <pre key={key}>{`Called tool: ${part.name}(${JSON.stringify(part.arguments, null, 2)})`}</pre>;
  }
  if (part.type === "tool_call_response") {
    return <pre key={key}>{`Tool result: ${JSON.stringify(part.result, null, 2)}`}</pre>;
  }
  return <pre key={key}>{JSON.stringify(part)}</pre>;
}

function renderMessage(message: Message, key: number) {
  return (
    <div key={key} className={`transcript-message transcript-message-${message.role}`}>
      <span className="transcript-role">{message.role}</span>
      <div className="transcript-parts">{message.parts.map((part, partIndex) => renderPart(part, partIndex))}</div>
    </div>
  );
}

export function QueueItemRow({ queueId, annotatorId, item, labels }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [showFullConversation, setShowFullConversation] = useState(false);
  const [labelId, setLabelId] = useState<number | null>(item.annotation?.label_id ?? null);
  const [description, setDescription] = useState(item.annotation?.description ?? "");
  const [saved, setSaved] = useState(item.annotation);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    setLabelId(item.annotation?.label_id ?? null);
    setDescription(item.annotation?.description ?? "");
    setSaved(item.annotation);
  }, [item, annotatorId]);

  const currentLabelName = labels.find((l) => l.id === saved?.label_id)?.name ?? "Ungraded";
  const isGraded = saved?.label_id != null;
  const hasStructuredContent = item.raw_row == null;

  const handleSaveAnnotation = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const result = await upsertQueueAnnotation(queueId, item.trace_id, item.span_id, {
        annotator_id: annotatorId,
        label_id: labelId,
        description,
      });
      setSaved(result);
    } catch (err) {
      setSaveError(String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className={`card interaction-row${expanded ? " interaction-row-expanded" : ""}`}>
      <button className="interaction-summary" onClick={() => setExpanded((v) => !v)}>
        <span className={`chevron${expanded ? " chevron-open" : ""}`} aria-hidden="true">
          ▸
        </span>
        <span className="timestamp">{new Date(item.start_timestamp).toLocaleString()}</span>
        <span className="preview">
          {item.unavailable ? "(trace no longer available)" : (item.input_text ?? "").slice(0, 120)}
        </span>
        <span className={`badge${isGraded ? " badge-accent" : " badge-neutral"}`}>{currentLabelName}</span>
      </button>

      {expanded && (
        <div className="interaction-detail">
          {item.unavailable ? (
            <div className="content-block content-block-warning">
              <h4>Trace no longer available</h4>
              <p>This item's trace has aged out of Logfire's 14-day query window and can't be displayed.</p>
            </div>
          ) : !hasStructuredContent ? (
            <div className="content-block content-block-warning">
              <h4>Raw row (no recognizable input/output shape)</h4>
              <pre>{JSON.stringify(item.raw_row, null, 2)}</pre>
            </div>
          ) : (
            <>
              <div className="content-grid">
                <div className="content-block">
                  <h4>Input</h4>
                  <div className="markdown-body">
                    <ReactMarkdown>{item.input_text ?? ""}</ReactMarkdown>
                  </div>
                </div>
                <div className="content-block">
                  <h4>Output</h4>
                  <div className="markdown-body">
                    <ReactMarkdown>{item.output_text ?? ""}</ReactMarkdown>
                  </div>
                </div>
              </div>

              <button className="btn-link" onClick={() => setShowFullConversation((v) => !v)}>
                {showFullConversation ? "Hide full conversation" : "View full conversation"}
              </button>
              {showFullConversation && (
                <div className="full-conversation">
                  {(item.full_conversation ?? []).map((message, index) => renderMessage(message, index))}
                </div>
              )}
            </>
          )}

          <div className="grading-panel">
            <h4>Grade</h4>
            <div className="label-picker">
              {labels.map((label) => (
                <button
                  key={label.id}
                  className={`chip${labelId === label.id ? " chip-selected" : ""}`}
                  onClick={() => setLabelId(label.id)}
                >
                  {label.name}
                </button>
              ))}
            </div>
            <textarea
              rows={4}
              placeholder="Why this label?"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
            <div className="grading-panel-footer">
              <button className="btn btn-primary" onClick={handleSaveAnnotation} disabled={saving}>
                {saving ? "Saving…" : "Save annotation"}
              </button>
              {item.trace_url && (
                <a className="btn-link trace-link" href={item.trace_url} target="_blank" rel="noopener noreferrer">
                  Open trace in Logfire ↗
                </a>
              )}
            </div>
            {saveError && <p className="error-inline">{saveError}</p>}
            {saved?.writeback_status === "failed" && (
              <p className="status-message status-message-warning">
                ⚠ Grade saved locally, but Logfire write-back failed: {saved.writeback_error}
              </p>
            )}
            {saved?.writeback_status === "written" && (
              <p className="status-message status-message-success">✓ Written to Logfire</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
