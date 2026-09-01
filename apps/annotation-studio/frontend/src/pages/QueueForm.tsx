import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { createQueue, getLogfireInfo, getQueue, listAnnotators, updateQueue } from "../api";
import type { LogfireInfo } from "../api";
import { AppHeader } from "../components/AppHeader";
import type { Annotator, Label } from "../types";

interface LabelDraft {
  id: number | null;
  name: string;
}

const QUERY_HELPERS: { label: string; snippet: string }[] = [
  {
    label: "Agent turn input/output",
    snippet:
      "SELECT trace_id, span_id, start_timestamp, duration, attributes FROM records\nWHERE span_name = 'invoke_agent your_agent_name'\nORDER BY start_timestamp DESC",
  },
  {
    label: "Tool calls",
    snippet:
      "SELECT trace_id, span_id, start_timestamp, duration, attributes FROM records\nWHERE span_name LIKE 'execute_tool %'\nORDER BY start_timestamp DESC",
  },
  {
    label: "Evaluation results (starting point — confirm against real data)",
    snippet:
      "SELECT trace_id, span_id, start_timestamp, duration, attributes FROM records\nWHERE span_name LIKE '%eval%'\nORDER BY start_timestamp DESC",
  },
];

export function QueueForm() {
  const { id: projectIdParam, queueId: queueIdParam } = useParams<{ id?: string; queueId?: string }>();
  const navigate = useNavigate();
  const isEdit = queueIdParam !== undefined;

  const [projectId, setProjectId] = useState<number | null>(projectIdParam ? Number(projectIdParam) : null);
  const [name, setName] = useState("");
  const [query, setQuery] = useState("");
  const [criteriaText, setCriteriaText] = useState("");
  const [samplingPercentage, setSamplingPercentage] = useState(100);
  const [labels, setLabels] = useState<LabelDraft[]>([
    { id: null, name: "Pass" },
    { id: null, name: "Fail" },
  ]);
  const [annotators, setAnnotators] = useState<Annotator[]>([]);
  const [assignedIds, setAssignedIds] = useState<number[]>([]);
  const [logfireInfo, setLogfireInfo] = useState<LogfireInfo | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listAnnotators().then(setAnnotators);
  }, []);

  useEffect(() => {
    // projectId is known immediately when creating a queue (from the URL) but only after the
    // queue itself loads when editing — either way, fetch once it's available so the Explore
    // link works in both flows.
    if (projectId === null) return;
    getLogfireInfo(projectId)
      .then(setLogfireInfo)
      .catch(() => setLogfireInfo(null));
  }, [projectId]);

  useEffect(() => {
    if (!isEdit || !queueIdParam) return;
    getQueue(Number(queueIdParam), null).then((queue) => {
      setProjectId(queue.project_id);
      setName(queue.name);
      setQuery(queue.query);
      setCriteriaText(queue.criteria_text);
      setSamplingPercentage(queue.sampling_percentage);
      setLabels(queue.labels.map((l: Label) => ({ id: l.id, name: l.name })));
      setAssignedIds(queue.annotator_ids);
    });
  }, [isEdit, queueIdParam]);

  const updateLabelName = (index: number, value: string) =>
    setLabels((prev) => prev.map((l, i) => (i === index ? { ...l, name: value } : l)));
  const removeLabel = (index: number) => setLabels((prev) => prev.filter((_, i) => i !== index));
  const addLabel = () => setLabels((prev) => [...prev, { id: null, name: "New label" }]);

  const toggleAnnotator = (annotatorId: number) =>
    setAssignedIds((prev) =>
      prev.includes(annotatorId) ? prev.filter((id) => id !== annotatorId) : [...prev, annotatorId],
    );

  const copyQuery = () => {
    navigator.clipboard?.writeText(query).catch(() => undefined);
  };

  const handleSave = async () => {
    setError(null);
    const trimmedLabels = labels.map((l) => ({ ...l, name: l.name.trim() }));
    if (trimmedLabels.some((l) => l.name.length === 0)) {
      setError("Label name cannot be empty");
      return;
    }
    setSaving(true);
    try {
      const draft = {
        name,
        query,
        criteria_text: criteriaText,
        sampling_percentage: samplingPercentage,
        labels: trimmedLabels,
        annotator_ids: assignedIds,
      };
      const saved = isEdit ? await updateQueue(Number(queueIdParam), draft) : await createQueue(projectId!, draft);
      navigate(`/queues/${saved.id}`);
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="page page-wide">
      <AppHeader />
      <main className="page-body">
        <h1>{isEdit ? "Edit queue" : "New queue"}</h1>

        <section className="card queue-form">
          <div className="field">
            <label htmlFor="queue-name">Name</label>
            <input id="queue-name" value={name} onChange={(e) => setName(e.target.value)} />
          </div>

          <div className="field">
            <label htmlFor="queue-query">Logfire SQL query</label>
            <p className="field-hint">
              A SELECT against Logfire's `records` table. Must return at least trace_id, span_id, start_timestamp.
            </p>
            <div className="query-helpers">
              {QUERY_HELPERS.map((helper) => (
                <button
                  key={helper.label}
                  type="button"
                  className="btn btn-dashed"
                  onClick={() => setQuery(helper.snippet)}
                >
                  {helper.label}
                </button>
              ))}
            </div>
            <textarea
              id="queue-query"
              className="query-textarea"
              rows={6}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <div className="query-actions">
              <button type="button" className="btn-link" onClick={copyQuery}>
                Copy query
              </button>
              {logfireInfo && (
                <a
                  className="btn-link"
                  href={`${logfireInfo.base_url}/${logfireInfo.organization_name}/${logfireInfo.project_name}/explore?q=${encodeURIComponent(query)}`}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Open in Logfire Explore ↗
                </a>
              )}
            </div>
          </div>

          <div className="field">
            <label htmlFor="queue-criteria">Grading criteria</label>
            <textarea
              id="queue-criteria"
              rows={6}
              value={criteriaText}
              onChange={(e) => setCriteriaText(e.target.value)}
            />
          </div>

          <div className="field">
            <label>Labels</label>
            <div className="label-chip-list">
              {labels.map((label, index) => (
                <div key={label.id ?? `new-${index}`} className="label-chip-row">
                  <input
                    className="label-chip-input"
                    value={label.name}
                    onChange={(e) => updateLabelName(index, e.target.value)}
                  />
                  <button className="btn-icon btn-icon-danger" onClick={() => removeLabel(index)} aria-label="Remove label">
                    ✕
                  </button>
                </div>
              ))}
            </div>
            <button className="btn btn-dashed btn-dashed-spaced" onClick={addLabel}>
              + Add label
            </button>
          </div>

          <div className="field">
            <label htmlFor="queue-sampling">Sampling percentage</label>
            <p className="field-hint">Of newly discovered matches, what percentage gets added to the queue.</p>
            <input
              id="queue-sampling"
              type="number"
              min={1}
              max={100}
              value={samplingPercentage}
              onChange={(e) => setSamplingPercentage(Number(e.target.value))}
            />
          </div>

          <div className="field">
            <label>Assigned annotators</label>
            <p className="field-hint">Leave empty to make this queue open to every annotator.</p>
            <div className="annotator-checkbox-list">
              {annotators.map((annotator) => (
                <label key={annotator.id} className="annotator-checkbox">
                  <input
                    type="checkbox"
                    checked={assignedIds.includes(annotator.id)}
                    onChange={() => toggleAnnotator(annotator.id)}
                  />
                  {annotator.name}
                </label>
              ))}
              {annotators.length === 0 && <p className="loading-text">No annotator profiles yet.</p>}
            </div>
          </div>

          <div className="card-footer">
            <button className="btn btn-primary" onClick={handleSave} disabled={saving || !name || !query}>
              {saving ? "Saving…" : "Save queue"}
            </button>
            {error && <p className="error-inline">{error}</p>}
          </div>
        </section>
      </main>
    </div>
  );
}
