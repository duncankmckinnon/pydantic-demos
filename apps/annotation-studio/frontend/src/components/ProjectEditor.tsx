import { useState } from "react";

import type { Label } from "../types";

interface LabelDraft {
  id: number | null;
  name: string;
}

interface Props {
  initialCriteriaText: string;
  initialAgentName: string;
  initialLabels: Label[];
  onSave: (values: { criteria_text: string; top_level_agent_name: string; labels: LabelDraft[] }) => Promise<void>;
}

export function ProjectEditor({ initialCriteriaText, initialAgentName, initialLabels, onSave }: Props) {
  const [criteriaText, setCriteriaText] = useState(initialCriteriaText);
  const [agentName, setAgentName] = useState(initialAgentName);
  const [labels, setLabels] = useState<LabelDraft[]>(initialLabels.map((l) => ({ id: l.id, name: l.name })));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const updateLabelName = (index: number, name: string) =>
    setLabels((prev) => prev.map((l, i) => (i === index ? { ...l, name } : l)));

  const removeLabel = (index: number) => setLabels((prev) => prev.filter((_, i) => i !== index));

  const moveUp = (index: number) =>
    setLabels((prev) => {
      if (index === 0) return prev;
      const next = [...prev];
      [next[index - 1], next[index]] = [next[index], next[index - 1]];
      return next;
    });

  const moveDown = (index: number) =>
    setLabels((prev) => {
      if (index === prev.length - 1) return prev;
      const next = [...prev];
      [next[index], next[index + 1]] = [next[index + 1], next[index]];
      return next;
    });

  const addLabel = () => setLabels((prev) => [...prev, { id: null, name: "New label" }]);

  const handleSave = async () => {
    setError(null);
    const trimmedLabels = labels.map((l) => ({ ...l, name: l.name.trim() }));
    if (trimmedLabels.some((l) => l.name.length === 0)) {
      setError("Label name cannot be empty");
      return;
    }
    setSaving(true);
    try {
      await onSave({
        criteria_text: criteriaText,
        top_level_agent_name: agentName,
        labels: trimmedLabels,
      });
    } catch (err) {
      // Deliberately does not replace loaded state on error — the reviewer's edits stay
      // in the form so a rejected save (400/409) doesn't lose their in-progress changes.
      setError(String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="project-editor">
      <label htmlFor="agent-name">Source agent name (Logfire span name suffix)</label>
      <input id="agent-name" value={agentName} onChange={(e) => setAgentName(e.target.value)} />

      <label htmlFor="criteria-text">Grading criteria</label>
      <textarea id="criteria-text" rows={8} value={criteriaText} onChange={(e) => setCriteriaText(e.target.value)} />

      <h3>Labels</h3>
      {labels.map((label, index) => (
        <div key={label.id ?? `new-${index}`} className="label-row">
          <input value={label.name} onChange={(e) => updateLabelName(index, e.target.value)} />
          <button onClick={() => moveUp(index)} disabled={index === 0}>
            ↑
          </button>
          <button onClick={() => moveDown(index)} disabled={index === labels.length - 1}>
            ↓
          </button>
          <button onClick={() => removeLabel(index)}>Remove</button>
        </div>
      ))}
      <button onClick={addLabel}>Add label</button>

      <button onClick={handleSave} disabled={saving}>
        {saving ? "Saving…" : "Save"}
      </button>
      {error && <p className="error">{error}</p>}
    </section>
  );
}
