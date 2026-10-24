// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { ArrowLeft, Download, Loader2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { MarkdownText } from "@/components/MarkdownText";
import { cn } from "@/lib/utils";

/** navin/share/files.py SHEETS_MIME: an Excel workbook read on the host. */
export const SHEETS_MIME = "application/vnd.navin.sheets+json";

export type PreviewKind =
  | "pdf"
  | "image"
  | "video"
  | "audio"
  | "markdown"
  | "csv"
  | "sheet"
  | "office"
  | "html"
  | "text";

const BY_EXT: Record<string, PreviewKind> = {
  pdf: "pdf",
  png: "image", jpg: "image", jpeg: "image", gif: "image", webp: "image", svg: "image", avif: "image", bmp: "image", ico: "image",
  mp4: "video", webm: "video", mov: "video", m4v: "video", ogv: "video",
  mp3: "audio", wav: "audio", ogg: "audio", oga: "audio", m4a: "audio", aac: "audio", flac: "audio", opus: "audio",
  md: "markdown", markdown: "markdown", mdx: "markdown",
  csv: "csv", tsv: "csv",
  xlsx: "sheet", xlsm: "sheet", xls: "sheet", ods: "sheet",
  docx: "office", doc: "office", odt: "office", pptx: "office", ppt: "office", odp: "office",
  html: "html", htm: "html",
};

const TEXT_EXTS = new Set([
  "txt", "log", "json", "jsonl", "yml", "yaml", "toml", "ini", "cfg", "conf", "xml", "sql", "sh", "bash", "zsh", "ps1", "bat",
  "js", "mjs", "cjs", "jsx", "ts", "tsx", "py", "rb", "php", "go", "rs", "java", "kt", "swift", "c", "h", "cc", "cpp", "hpp",
  "cs", "css", "scss", "less", "vue", "svelte", "astro", "graphql", "proto", "tf", "lock", "gitignore", "dockerignore", "rst",
]);
const TEXT_NAMES = new Set(["dockerfile", "makefile", "procfile", "license", "readme", "changelog"]);

/** How the guest viewer shows a file, or null when it only downloads. */
export function previewKind(path: string): PreviewKind | null {
  const name = (path.split("/").pop() || "").toLowerCase();
  const dot = name.lastIndexOf(".");
  const ext = dot >= 0 ? name.slice(dot + 1) : "";
  if (BY_EXT[ext]) return BY_EXT[ext];
  if (TEXT_EXTS.has(ext) || (!ext && TEXT_NAMES.has(name))) return "text";
  return null;
}

/** Comma or tab separated text as rows, quotes handled. */
export function parseDelimited(text: string, delimiter: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') {
        cell += '"';
        i += 1;
      } else if (ch === '"') {
        quoted = false;
      } else {
        cell += ch;
      }
    } else if (ch === '"' && cell === "") {
      quoted = true;
    } else if (ch === delimiter) {
      row.push(cell);
      cell = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i += 1;
      row.push(cell);
      rows.push(row);
      row = [];
      cell = "";
    } else {
      cell += ch;
    }
  }
  if (cell !== "" || row.length) {
    row.push(cell);
    rows.push(row);
  }
  return rows;
}

const MAX_TEXT_CHARS = 2_000_000;
const MAX_TABLE_ROWS = 2_000;

type Loaded =
  | { state: "loading" }
  | { state: "error"; code: string }
  | { state: "ready"; url: string; mime: string; text: string | null; sheets: Sheet[] | null };

interface Sheet {
  name: string;
  rows: string[][];
  truncated?: boolean;
  hidden?: boolean;
}

function Table({ rows, truncated }: { rows: string[][]; truncated?: boolean }) {
  const { t } = useTranslation();
  const shown = rows.slice(0, MAX_TABLE_ROWS);
  const [head, ...body] = shown;
  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <table className="min-w-full border-collapse text-[12.5px]">
        {head ? (
          <thead className="sticky top-0 z-[1] bg-muted">
            <tr>
              {head.map((cell, index) => (
                <th key={index} className="whitespace-nowrap border border-border/60 px-2 py-1 text-start font-semibold">{cell}</th>
              ))}
            </tr>
          </thead>
        ) : null}
        <tbody>
          {body.map((row, r) => (
            <tr key={r} className="odd:bg-muted/30">
              {row.map((cell, c) => (
                <td key={c} className="whitespace-pre-wrap border border-border/50 px-2 py-1 align-top">{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {truncated || rows.length > MAX_TABLE_ROWS ? (
        <p className="px-3 py-2 text-[11.5px] text-muted-foreground">
          {t("chat.liveShare.guest.viewer.tableTruncated", { defaultValue: "Only the first rows are shown. Download the file for the rest." })}
        </p>
      ) : null}
    </div>
  );
}

function Sheets({ sheets }: { sheets: Sheet[] }) {
  const visible = sheets.filter((sheet) => !sheet.hidden);
  const list = visible.length ? visible : sheets;
  const [active, setActive] = useState(0);
  const sheet = list[Math.min(active, list.length - 1)];
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {list.length > 1 ? (
        <div className="flex gap-1 overflow-x-auto border-b border-border/60 px-2 py-1" role="tablist">
          {list.map((item, index) => (
            <button
              key={item.name}
              type="button"
              role="tab"
              aria-selected={index === active}
              onClick={() => setActive(index)}
              className={cn(
                "whitespace-nowrap rounded px-2 py-0.5 text-[12px]",
                index === active ? "bg-emerald-600 text-white dark:bg-emerald-500" : "text-muted-foreground hover:bg-muted",
              )}
            >
              {item.name}
            </button>
          ))}
        </div>
      ) : null}
      {sheet ? <Table rows={sheet.rows} truncated={sheet.truncated} /> : null}
    </div>
  );
}

/**
 * A shared project file shown inside the panel: no new tab, so no popup
 * blocker, and HTML runs in a sandbox with no script and no access to the
 * page. Word, PowerPoint and old spreadsheets arrive as a PDF rendered on the
 * host, Excel workbooks as a grid.
 */
export function GuestFileViewer({
  path,
  kind,
  fetchPreview,
  onDownload,
  onClose,
}: {
  path: string;
  kind: PreviewKind;
  fetchPreview: (path: string) => Promise<{ blob: Blob; name: string }>;
  onDownload: () => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [loaded, setLoaded] = useState<Loaded>({ state: "loading" });
  const name = path.split("/").pop() || path;

  useEffect(() => {
    let cancelled = false;
    let url = "";
    setLoaded({ state: "loading" });
    void (async () => {
      try {
        const { blob } = await fetchPreview(path);
        const mime = blob.type;
        let text: string | null = null;
        let sheets: Sheet[] | null = null;
        if (mime === SHEETS_MIME) {
          sheets = (JSON.parse(await blob.text()) as { sheets: Sheet[] }).sheets;
        } else if (kind === "markdown" || kind === "csv" || kind === "text" || kind === "html") {
          text = (await blob.text()).slice(0, MAX_TEXT_CHARS);
        }
        // An image or a PDF keeps a typed blob so the browser renders it.
        url = URL.createObjectURL(blob);
        if (!cancelled) setLoaded({ state: "ready", url, mime, text, sheets });
      } catch (reason) {
        if (!cancelled) setLoaded({ state: "error", code: reason instanceof Error ? reason.message : "" });
      }
    })();
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [fetchPreview, kind, path]);

  const body = useMemo(() => {
    if (loaded.state === "loading") {
      return (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 text-[13px] text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" aria-hidden />
          {kind === "office" || kind === "sheet"
            ? t("chat.liveShare.guest.viewer.rendering", { defaultValue: "Preparing the preview..." })
            : t("chat.liveShare.guest.viewer.loading", { defaultValue: "Loading..." })}
        </div>
      );
    }
    if (loaded.state === "error") {
      const message = loaded.code === "no_preview"
        ? t("chat.liveShare.guest.viewer.noPreview", { defaultValue: "No preview for this file on the host's computer. Download it to open it." })
        : loaded.code === "too_large"
          ? t("chat.liveShare.guest.files.tooLarge", { defaultValue: "This file is too large to send through the share (40 MB max)." })
          : t("chat.liveShare.guest.files.failed", { defaultValue: "This file could not be sent. Try again." });
      return <p className="flex flex-1 items-center justify-center p-6 text-center text-[13px] text-muted-foreground">{message}</p>;
    }
    const { url, mime, text, sheets } = loaded;
    if (sheets) return <Sheets sheets={sheets} />;
    if (mime === "application/pdf" || kind === "pdf") {
      return <iframe title={name} src={url} className="min-h-0 w-full flex-1 border-0 bg-white" />;
    }
    if (kind === "image") {
      return (
        <div className="flex min-h-0 flex-1 items-center justify-center overflow-auto bg-muted/30 p-3">
          <img src={url} alt={name} className="max-h-full max-w-full object-contain" />
        </div>
      );
    }
    if (kind === "video") {
      return (
        <div className="flex min-h-0 flex-1 items-center justify-center bg-black">
          <video src={url} controls className="max-h-full max-w-full" />
        </div>
      );
    }
    if (kind === "audio") {
      return (
        <div className="flex flex-1 items-center justify-center p-6">
          <audio src={url} controls className="w-full max-w-md" />
        </div>
      );
    }
    if (kind === "markdown") {
      return (
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3 text-[14px]">
          <MarkdownText>{text ?? ""}</MarkdownText>
        </div>
      );
    }
    if (kind === "csv") {
      const delimiter = path.toLowerCase().endsWith(".tsv") ? "\t" : ",";
      return <Table rows={parseDelimited(text ?? "", delimiter)} />;
    }
    if (kind === "html") {
      // sandbox="" : no script, no form, no top navigation, an opaque origin.
      return <iframe title={name} sandbox="" srcDoc={text ?? ""} className="min-h-0 w-full flex-1 border-0 bg-white" />;
    }
    return (
      <pre className="min-h-0 flex-1 overflow-auto whitespace-pre px-3 py-2 font-mono text-[12.5px] leading-relaxed">{text}</pre>
    );
  }, [kind, loaded, name, path, t]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-border/60 px-2 py-1.5">
        <button
          type="button"
          onClick={onClose}
          aria-label={t("chat.liveShare.guest.viewer.back", { defaultValue: "Back to the files" })}
          title={t("chat.liveShare.guest.viewer.back", { defaultValue: "Back to the files" })}
          className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-muted"
        >
          <ArrowLeft className="rtl:-scale-x-100 h-4 w-4" aria-hidden />
        </button>
        <span className="min-w-0 flex-1 truncate text-[13px] font-medium" title={path}>{name}</span>
        <button
          type="button"
          onClick={onDownload}
          className="flex h-8 items-center gap-1.5 rounded-md bg-emerald-600 px-2.5 text-[12.5px] font-medium text-white hover:bg-emerald-700 dark:bg-emerald-500 dark:hover:bg-emerald-400"
        >
          <Download className="h-3.5 w-3.5" aria-hidden />
          {t("chat.liveShare.guest.files.download", { defaultValue: "Download" })}
        </button>
      </div>
      {body}
    </div>
  );
}
