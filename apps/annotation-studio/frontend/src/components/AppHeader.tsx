import { Link } from "react-router-dom";

import { useAnnotator } from "../annotator";

export function AppHeader() {
  const { annotators, selectedId } = useAnnotator();
  const selectedName = annotators.find((a) => a.id === selectedId)?.name;

  return (
    <header className="app-header">
      <Link to="/">
        <h1>Annotation Studio</h1>
      </Link>
      <Link to="/annotators">{selectedName ?? "Choose annotator"}</Link>
    </header>
  );
}
