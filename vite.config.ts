import { defineConfig } from "vite";

// Relative base so the site works on GitHub Pages under /<repo>/ as well as at a root domain.
// MapLibre is ~1 MB on its own; that's expected, so raise the warning threshold.
export default defineConfig({ base: "./", build: { chunkSizeWarningLimit: 1500 } });
