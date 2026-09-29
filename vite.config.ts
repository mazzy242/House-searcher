import { defineConfig } from "vite";

// Relative base so the site works on GitHub Pages under /<repo>/ as well as at a root domain.
// MapLibre is ~1 MB on its own (plus its worker); that's expected, so raise the warning threshold.
export default defineConfig({
  base: "./",
  worker: { format: "es" },
  build: { chunkSizeWarningLimit: 1500 },
});
