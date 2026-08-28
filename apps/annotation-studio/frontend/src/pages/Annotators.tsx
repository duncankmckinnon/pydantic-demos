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
    <div className="annotators-page">
      <AppHeader />
      <h1>Choose annotator</h1>
      {error && <p className="error">{error}</p>}
      {annotators.map((annotator) => (
        <div key={annotator.id} className="annotator-row">
          <button
            className={annotator.id === selectedId ? "selected" : ""}
            onClick={() => setSelectedId(annotator.id)}
          >
            {annotator.id === selectedId ? "Selected" : "Select"}
          </button>
          <input
            defaultValue={annotator.name}
            onBlur={(e) => {
              const value = e.target.value.trim();
              if (value && value !== annotator.name) handleRename(annotator.id, value);
            }}
          />
          <button onClick={() => handleDelete(annotator.id)}>Remove</button>
        </div>
      ))}
      <div className="annotator-row">
        <input
          placeholder="New annotator name"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleCreate()}
        />
        <button onClick={handleCreate} disabled={!newName.trim()}>
          Add
        </button>
      </div>
    </div>
  );
}
