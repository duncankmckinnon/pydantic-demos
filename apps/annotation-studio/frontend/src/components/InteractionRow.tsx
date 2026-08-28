import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";

import { upsertAnnotation } from "../api";
import type { Interaction, Label, Message, MessagePart } from "../types";

interface Props {
  projectId: number;
  annotatorId: number;
  interaction: Interaction;
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
    <div key={key} className={`message message-${message.role}`}>
      <strong>{message.role}</strong>
      {message.parts.map((part, partIndex) => renderPart(part, partIndex))}
    </div>
  );
}

export function InteractionRow({ projectId, annotatorId, interaction, labels }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [showFullConversation, setShowFullConversation] = useState(false);
  const [labelId, setLabelId] = useState<number | null>(interaction.annotation?.label_id ?? null);
  const [description, setDescription] = useState(interaction.annotation?.description ?? "");
  const [saved, setSaved] = useState(interaction.annotation);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    // The interaction/annotator this row shows can change (pagination reload, switching
    // annotator) — resync local edit state to the freshly-loaded annotation each time.
    setLabelId(interaction.annotation?.label_id ?? null);
    setDescription(interaction.annotation?.description ?? "");
    setSaved(interaction.annotation);
  }, [interaction, annotatorId]);

  const currentLabelName = labels.find((l) => l.id === saved?.label_id)?.name ?? "Ungraded";

  const handleSaveAnnotation = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const result = await upsertAnnotation(projectId, interaction.trace_id, interaction.span_id, {
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
    <div className="interaction-row">
      <button className="interaction-summary" onClick={() => setExpanded((v) => !v)}>
        <span className="timestamp">{new Date(interaction.start_timestamp).toLocaleString()}</span>
        <span className="preview">{interaction.input_text.slice(0, 120)}</span>
        <span className="label-badge">{currentLabelName}</span>
      </button>

      {expanded && (
        <div className="interaction-detail">
          {interaction.raw_attributes ? (
            <>
              <h4>Raw attributes (message parsing failed)</h4>
              <pre>{JSON.stringify(interaction.raw_attributes, null, 2)}</pre>
            </>
          ) : (
            <>
              <h4>Input</h4>
              <ReactMarkdown>{interaction.input_text}</ReactMarkdown>

              <h4>Output</h4>
              <ReactMarkdown>{interaction.output_text}</ReactMarkdown>

              <button onClick={() => setShowFullConversation((v) => !v)}>
                {showFullConversation ? "Hide full conversation" : "View full conversation"}
              </button>
              {showFullConversation && (
                <div className="full-conversation">
                  {interaction.full_conversation.map((message, index) => renderMessage(message, index))}
                </div>
              )}
            </>
          )}

          <h4>Grade</h4>
          <div className="label-picker">
            {labels.map((label) => (
              <button
                key={label.id}
                className={labelId === label.id ? "selected" : ""}
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
          <button onClick={handleSaveAnnotation} disabled={saving}>
            {saving ? "Saving…" : "Save annotation"}
          </button>
          {saveError && <p className="error">{saveError}</p>}

          {saved?.writeback_status === "failed" && (
            <p className="writeback-warning">
              Grade saved locally, but Logfire write-back failed: {saved.writeback_error}
            </p>
          )}
          {saved?.writeback_status === "written" && <p className="writeback-ok">Written to Logfire</p>}

          <a href={interaction.trace_url} target="_blank" rel="noopener noreferrer">
            Open trace in Logfire ↗
          </a>
        </div>
      )}
    </div>
  );
}
