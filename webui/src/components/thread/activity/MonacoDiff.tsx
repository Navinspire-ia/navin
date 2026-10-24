// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { DiffEditor, loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor";
import EditorWorker from "monaco-editor/editor/editor.worker?worker";

// Bundle Monaco locally (desktop works offline): no CDN loader.
(self as unknown as { MonacoEnvironment: unknown }).MonacoEnvironment = {
  getWorker: () => new EditorWorker(),
};
loader.config({ monaco });

export default function MonacoDiff({
  original,
  modified,
  language,
  sideBySide,
  dark,
}: {
  original: string;
  modified: string;
  language?: string;
  sideBySide: boolean;
  dark: boolean;
}) {
  return (
    <DiffEditor
      original={original}
      modified={modified}
      language={language}
      theme={dark ? "vs-dark" : "vs"}
      options={{
        readOnly: true,
        renderSideBySide: sideBySide,
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        fontFamily: '"JetBrains Mono Variable", "JetBrains Mono", monospace',
        fontSize: 12,
        hideUnchangedRegions: { enabled: true },
        automaticLayout: true,
      }}
    />
  );
}
