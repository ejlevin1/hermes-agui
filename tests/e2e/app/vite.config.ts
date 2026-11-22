// ONE js (+ css) with fixed names, no chunks; same conventions as the hosts' web builds.
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
export default defineConfig({
  base: "./",
  plugins: [react()],
  build: {
    outDir: "dist", emptyOutDir: true, target: "es2022", sourcemap: false, modulePreload: false, cssCodeSplit: false,
    rollupOptions: { input: "index.html", output: { entryFileNames: "app.js", inlineDynamicImports: true,
      assetFileNames: (i) => ((i.names ?? []).some((n) => n.endsWith(".css")) ? "app.css" : "assets/[name][extname]") } },
  },
});
