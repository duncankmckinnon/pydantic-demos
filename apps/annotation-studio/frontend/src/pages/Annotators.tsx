import { useState } from "react";

import { createAnnotator, deleteAnnotator, renameAnnotator } from "../api";
import { useAnnotator } from "../annotator";
import { AppHeader } from "../components/AppHeader";

export function Annotators() {
  const { annotators, selectedId, setSelectedId, refresh } = useAnnotator();
  const [newName, setNewName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const handleCreate = async () => {
    setError(null);
    try {
      await createAnnotator(newName.trim());
      setNewName("");
      refresh();
    } catch (err) {
      setError(String(err));
    }
  };

  const handleRename = async (id: number, name: string) => {
    setError(null);
    try {
      await renameAnnotator(id, name);
      refresh();
    } catch (err) {
      setError(String(err));
    }
  };

  const handleDelete = async (id: number) => {
    setError(null);
    try {
      await deleteAnnotator(id);
      refresh();
    } catch (err) {
      setError(String(err));
    }
  };

  return (
    <div className="page">
      <AppHeader />
      <main className="page-body page-body-narrow">
        <div className="page-heading">
          <h1>Choose annotator</h1>
          <p className="page-subtitle">Pick a profile to grade as, or create a new one.</p>
        </div>
        {error && <p className="error-banner">{error}</p>}
        <div className="annotator-list">
          {annotators.map((annotator) => (
            <div
              key={annotator.id}
              className={`annotator-card${annotator.id === selectedId ? " annotator-card-selected" : ""}`}
            >
              <span className="avatar-circle" aria-hidden="true">
                {annotator.name.slice(0, 1).toUpperCase()}
              </span>
              <input
                className="annotator-name-input"
                defaultValue={annotator.name}
                onBlur={(e) => {
                  const value = e.target.value.trim();
                  if (value && value !== annotator.name) handleRename(annotator.id, value);
                }}
              />
              <div className="annotator-card-actions">
                <button
                  className={annotator.id === selectedId ? "btn btn-primary btn-sm" : "btn btn-secondary btn-sm"}
                  onClick={() => setSelectedId(annotator.id)}
                >
                  {annotator.id === selectedId ? "Selected" : "Select"}
                </button>
                <button className="btn btn-ghost btn-sm btn-danger-ghost" onClick={() => handleDelete(annotator.id)}>
                  Remove
                </button>
              </div>
            </div>
          ))}
        </div>
        <div className="annotator-card annotator-card-new">
          <span className="avatar-circle avatar-circle-new" aria-hidden="true">
            +
          </span>
          <input
            className="annotator-name-input"
            placeholder="New annotator name"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleCreate()}
          />
          <div className="annotator-card-actions">
            <button className="btn btn-primary btn-sm" onClick={handleCreate} disabled={!newName.trim()}>
              Add
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}
