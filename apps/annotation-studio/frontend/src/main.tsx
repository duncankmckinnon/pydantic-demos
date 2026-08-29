import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { AnnotatorProvider } from "./annotator";
import { App } from "./App";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <AnnotatorProvider>
        <App />
      </AnnotatorProvider>
    </BrowserRouter>
  </StrictMode>,
);
