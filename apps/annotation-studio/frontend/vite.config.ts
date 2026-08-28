import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
  build: {
    // Write straight into the path main.py serves from (src/annotation_studio/static/dist)
    // so a local `npm run build` is directly loadable from the FastAPI port with no
    // Docker/copy step in between.
    outDir: "../src/annotation_studio/static/dist",
    emptyOutDir: true,
  },
});
