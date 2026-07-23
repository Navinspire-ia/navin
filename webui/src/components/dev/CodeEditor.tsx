import CodeMirror from "@uiw/react-codemirror";
import { loadLanguage, type LanguageName } from "@uiw/codemirror-extensions-langs";
import { vscodeDark, vscodeLight } from "@uiw/codemirror-theme-vscode";
import { linter, lintGutter, type Diagnostic } from "@codemirror/lint";
import { EditorSelection, type Text } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import { useEffect, useMemo, useRef } from "react";

import type { FileDiagnostic } from "@/lib/types";

const LANGUAGE_ALIASES: Record<string, LanguageName> = {
  bash: "bash",
  dockerfile: "bash",
  golang: "go",
  javascript: "js",
  json: "json",
  jsonl: "json",
  jsx: "jsx",
  markdown: "markdown",
  python: "python",
  scss: "scss",
  sh: "sh",
  shell: "sh",
  text: "textile",
  tsx: "tsx",
  typescript: "ts",
  yaml: "yaml",
  yml: "yml",
};

function docPosition(doc: Text, line: number, col: number): number {
  const lineNumber = Math.min(Math.max(line, 1), doc.lines);
  const info = doc.line(lineNumber);
  return Math.min(info.from + Math.max(col - 1, 0), info.to);
}

function toCmDiagnostics(doc: Text, rows: FileDiagnostic[]): Diagnostic[] {
  const out: Diagnostic[] = [];
  for (const row of rows) {
    const from = docPosition(doc, row.line, row.col);
    let to = docPosition(doc, row.end_line, row.end_col);
    if (to <= from) to = Math.min(from + 1, doc.length);
    out.push({
      from,
      to,
      severity: row.severity === "error" ? "error" : "warning",
      message: row.code ? `${row.code}: ${row.message}` : row.message,
    });
  }
  return out;
}

export default function CodeEditor({
  value,
  language,
  isDark,
  readOnly,
  diagnostics,
  reveal,
  onChange,
  onSave,
}: {
  value: string;
  language: string;
  isDark: boolean;
  readOnly?: boolean;
  diagnostics?: FileDiagnostic[];
  reveal?: { line: number; nonce: number } | null;
  onChange: (next: string) => void;
  onSave: () => void;
}) {
  const viewRef = useRef<EditorView | null>(null);

  useEffect(() => {
    if (!reveal) return;
    const view = viewRef.current;
    if (!view) return;
    const doc = view.state.doc;
    const lineNumber = Math.min(Math.max(reveal.line, 1), doc.lines);
    const info = doc.line(lineNumber);
    view.dispatch({
      selection: EditorSelection.cursor(info.from),
      effects: EditorView.scrollIntoView(info.from, { y: "center" }),
    });
  }, [reveal, value]);

  const extensions = useMemo(() => {
    const normalized = (language || "").toLowerCase();
    const name = LANGUAGE_ALIASES[normalized] ?? (normalized as LanguageName);
    const list = [];
    try {
      const lang = loadLanguage(name);
      if (lang) list.push(lang);
    } catch {
      // Unknown language: plain text.
    }
    if (diagnostics && diagnostics.length) {
      const rows = diagnostics;
      list.push(
        lintGutter(),
        linter((view) => toCmDiagnostics(view.state.doc, rows), { delay: 0 }),
      );
    }
    return list;
  }, [diagnostics, language]);

  return (
    <div
      className="h-full min-h-0 [&_.cm-editor]:h-full [&_.cm-editor]:text-[13px] [&_.cm-scroller]:overflow-auto"
      onKeyDown={(event) => {
        if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
          event.preventDefault();
          onSave();
        }
      }}
    >
      <CodeMirror
        value={value}
        height="100%"
        theme={isDark ? vscodeDark : vscodeLight}
        extensions={extensions}
        readOnly={readOnly}
        onCreateEditor={(view) => {
          viewRef.current = view;
        }}
        onChange={onChange}
        basicSetup={{
          lineNumbers: true,
          foldGutter: true,
          highlightActiveLine: true,
          highlightSelectionMatches: true,
          autocompletion: false,
        }}
      />
    </div>
  );
}
