// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/** The live share guest page, served by the share relay (navin/share/relay.py)
 *  at /s/<share_id>. Built into navin/share/guest: npm run build:guest. */
import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // The relay serves nothing but guest.html and /assets: no public dir.
  publicDir: false,
  resolve: {
    alias: [
      // One language fetched on demand instead of every locale in the first script.
      { find: /^@\/i18n$/, replacement: path.resolve(__dirname, "./src/guest/guest-i18n.ts") },
      { find: "@", replacement: path.resolve(__dirname, "./src") },
    ],
    dedupe: ["react", "react-dom"],
  },
  build: {
    outDir: path.resolve(__dirname, "../navin/share/guest"),
    emptyOutDir: true,
    sourcemap: false,
    rollupOptions: { input: path.resolve(__dirname, "guest.html") },
  },
});
