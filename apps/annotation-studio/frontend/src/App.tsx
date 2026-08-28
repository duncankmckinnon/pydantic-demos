import { Route, Routes } from "react-router-dom";

import { Annotators } from "./pages/Annotators";
import { ProjectDetail } from "./pages/ProjectDetail";
import { ProjectList } from "./pages/ProjectList";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<ProjectList />} />
      <Route path="/annotators" element={<Annotators />} />
      <Route path="/projects/:id" element={<ProjectDetail />} />
    </Routes>
  );
}
