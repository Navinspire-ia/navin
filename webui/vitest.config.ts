import path from "node:path";
import { defineConfig } from "vitest/config";

// The app's vite.config.ts exports a function (it reads env for the dev
// proxy), which vitest does not always evaluate when resolving aliases for
// test files. This standalone config restates the single alias the tests
// need so `@/...` imports resolve the same way they do in the app build.
export default defineConfig({
  // Never share `node_modules/.vite` with the dev server. Vitest prunes its
  // cache directory, which wipes the pre-bundled dependencies a running `npm
  // run dev` still serves: every lazily loaded panel (the CodeMirror editor
  // first) then answers 504 until Vite is restarted.
  cacheDir: path.resolve(__dirname, "node_modules/.vitest"),
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    dir: __dirname,
  },
});
