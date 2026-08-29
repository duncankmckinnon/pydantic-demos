import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { listAnnotators } from "./api";
import type { Annotator } from "./types";

const STORAGE_KEY = "annotation-studio.annotator-id";

interface AnnotatorContextValue {
  annotators: Annotator[];
  selectedId: number | null;
  setSelectedId: (id: number | null) => void;
  refresh: () => void;
}

const AnnotatorContext = createContext<AnnotatorContextValue | null>(null);

function readStoredId(): number | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const n = Number(raw);
    return Number.isInteger(n) && n > 0 ? n : null;
  } catch {
    return null;
  }
}

export function AnnotatorProvider({ children }: { children: ReactNode }) {
  const [annotators, setAnnotators] = useState<Annotator[]>([]);
  const [selectedId, setSelectedIdState] = useState<number | null>(readStoredId);

  const refresh = () => {
    listAnnotators().then(setAnnotators);
  };

  useEffect(refresh, []);

  useEffect(() => {
    // If the stored profile was deleted (e.g. from another tab), clear the selection
    // rather than keep pointing at an id that no longer exists.
    if (selectedId !== null && annotators.length > 0 && !annotators.some((a) => a.id === selectedId)) {
      setSelectedIdState(null);
    }
  }, [annotators, selectedId]);

  const setSelectedId = (id: number | null) => {
    setSelectedIdState(id);
    try {
      if (id === null) localStorage.removeItem(STORAGE_KEY);
      else localStorage.setItem(STORAGE_KEY, String(id));
    } catch {
      // localStorage unavailable — selection just won't survive a reload.
    }
  };

  return (
    <AnnotatorContext.Provider value={{ annotators, selectedId, setSelectedId, refresh }}>
      {children}
    </AnnotatorContext.Provider>
  );
}

export function useAnnotator(): AnnotatorContextValue {
  const value = useContext(AnnotatorContext);
  if (!value) throw new Error("useAnnotator must be used within AnnotatorProvider");
  return value;
}
