import { Link } from "react-router-dom";

import { useAnnotator } from "../annotator";

export function AppHeader() {
  const { annotators, selectedId } = useAnnotator();
  const selectedName = annotators.find((a) => a.id === selectedId)?.name;

  return (
    <header className="app-header">
      <Link to="/" className="app-brand">
        <span className="app-logo" aria-hidden="true">
          AS
        </span>
        <span className="app-title">Annotation Studio</span>
      </Link>
      <Link to="/annotators" className="annotator-pill">
        <span className="annotator-pill-dot" aria-hidden="true" />
        {selectedName ?? "Choose annotator"}
      </Link>
    </header>
  );
}
