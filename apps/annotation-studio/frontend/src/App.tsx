import { Route, Routes } from "react-router-dom";

import { Annotators } from "./pages/Annotators";
import { ProjectDetail } from "./pages/ProjectDetail";
import { ProjectList } from "./pages/ProjectList";
import { QueueDetail } from "./pages/QueueDetail";
import { QueueForm } from "./pages/QueueForm";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<ProjectList />} />
      <Route path="/annotators" element={<Annotators />} />
      <Route path="/projects/:id" element={<ProjectDetail />} />
      <Route path="/projects/:id/queues/new" element={<QueueForm />} />
      <Route path="/queues/:queueId" element={<QueueDetail />} />
      <Route path="/queues/:queueId/edit" element={<QueueForm />} />
    </Routes>
  );
}
