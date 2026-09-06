/**
 * Notes editor: Tiptap 3 over markdown storage.
 *
 * The note body lives as markdown on disk (portable, agent-editable); this
 * component renders it as rich blocks, autosaves with a conflict token, and
 * offers a "/" quick-insert menu.
 */

import {
  createContext,
  forwardRef,
  useCallback,
  useContext,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  EditorContent,
  NodeViewContent,
  NodeViewWrapper,
  ReactNodeViewRenderer,
  useEditor,
  type Editor,
  type NodeViewProps,
} from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { CodeBlockLowlight } from "@tiptap/extension-code-block-lowlight";
import Highlight from "@tiptap/extension-highlight";
import Image from "@tiptap/extension-image";
import Placeholder from "@tiptap/extension-placeholder";
import { Table, TableCell, TableHeader, TableRow } from "@tiptap/extension-table";
import TaskItem from "@tiptap/extension-task-item";
import TaskList from "@tiptap/extension-task-list";
import { Color, TextStyle } from "@tiptap/extension-text-style";
import { common, createLowlight } from "lowlight";
import { Markdown } from "tiptap-markdown";
import {
  extractImageFilesFromDrop,
  extractImageFilesFromPaste,
} from "@/hooks/useClipboardAndDrop";
import {
  Bold,
  Bot,
  CheckSquare,
  Code2,
  Eraser,
  FileUp,
  Heading1,
  Heading2,
  Heading3,
  Highlighter,
  ImagePlus,
  Italic,
  Link2,
  List,
  ListOrdered,
  Minus,
  Pilcrow,
  Play,
  Quote,
  Table2,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { openExternalUrl } from "@/lib/api";
import {
  noteAttachmentUrl,
  uploadNoteAttachment,
} from "@/lib/notes-api";
import { cn } from "@/lib/utils";
import {
  AGENT_BLOCK_LANGUAGE,
  agentBlockTemplate,
} from "@/components/notes/agent-block";
import {
  filterSlashCommands,
  slashQueryFromBlockText,
  type SlashCommandId,
  type SlashCommandItem,
} from "@/components/notes/slash-commands";
import {
  createWikilinkExtension,
  filterWikilinkTargets,
  wikilinkQueryAt,
  type WikilinkTarget,
} from "@/components/notes/wikilink-extension";
import "./notes-editor.css";

const lowlight = createLowlight(common);

/**
 * Priority palettes for text colour and highlight. Alpha backgrounds keep
 * highlighted text readable in both light and dark themes; the values are
 * serialized as inline HTML in the markdown (html mode) so they round-trip.
 */
const TEXT_COLORS = [
  { id: "red", value: "#ef4444" },
  { id: "orange", value: "#f97316" },
  { id: "amber", value: "#d97706" },
  { id: "green", value: "#22c55e" },
  { id: "blue", value: "#3b82f6" },
  { id: "purple", value: "#a855f7" },
] as const;

const HIGHLIGHT_COLORS = [
  { id: "red", value: "#ef44443d" },
  { id: "orange", value: "#f973163d" },
  { id: "amber", value: "#eab3083d" },
  { id: "green", value: "#22c55e3d" },
  { id: "blue", value: "#3b82f63d" },
  { id: "purple", value: "#a855f73d" },
] as const;

/** Prefix bare domains so inserted links always have a scheme. */
function normalizeHref(raw: string): string {
  const url = raw.trim();
  if (!url) return "";
  if (/^(https?:|mailto:|#|\/|_files\/)/i.test(url)) return url;
  return `https://${url}`;
}

/**
 * Markdown of the current doc, or null when unavailable.
 *
 * Tiptap v3 empties `extensionStorage` on destroy, and `useEditor` with a deps
 * array recreates the editor on every note switch - so effects and update
 * callbacks can briefly hold a destroyed instance whose `storage.markdown` is
 * gone. Reading through this helper instead of `storage.markdown.getMarkdown()`
 * directly is what keeps note switching from crashing the app.
 */
function markdownOf(editor: Editor): string | null {
  if (editor.isDestroyed) return null;
  const storage = (editor.storage as unknown as Record<string, unknown>)
    .markdown as { getMarkdown: () => string } | undefined;
  return storage ? storage.getMarkdown() : null;
}

/**
 * Run callback for agent blocks, provided by the workbench.
 *
 * Node views render through EditorContent's portals, so React context is the
 * clean channel between the workbench and a button living inside the doc.
 */
const AgentRunContext = createContext<((spec: string) => void) | null>(null);

/**
 * Format-bar button: onMouseDown + preventDefault keeps the editor selection
 * alive while clicking, which is the whole point of a selection toolbar.
 */
function BarButton({
  label,
  active = false,
  onPress,
  children,
}: {
  label: string;
  active?: boolean;
  onPress: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      aria-pressed={active}
      onMouseDown={(event) => {
        event.preventDefault();
        onPress();
      }}
      className={cn(
        "flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-foreground/80 transition-colors hover:bg-muted",
        active && "bg-muted text-foreground",
      )}
    >
      {children}
    </button>
  );
}

/**
 * Node view for every code block. Ordinary languages keep the plain
 * pre/code rendering (lowlight decorations still apply); the "agent"
 * language becomes a card with a Run button that hands the block text to
 * the chat.
 */
function CodeBlockView(props: NodeViewProps) {
  const { t } = useTranslation();
  const onRun = useContext(AgentRunContext);
  const language = String(props.node.attrs.language ?? "");

  if (language !== AGENT_BLOCK_LANGUAGE) {
    return (
      <NodeViewWrapper as="pre">
        <NodeViewContent<"code"> as="code" />
      </NodeViewWrapper>
    );
  }

  return (
    <NodeViewWrapper className="notes-agent-block" data-type="agent-block">
      <div className="notes-agent-block-header" contentEditable={false}>
        <Bot className="h-3.5 w-3.5" aria-hidden />
        <span className="notes-agent-block-title">
          {t("notes.agent.blockTitle", { defaultValue: "Agent block" })}
        </span>
        {onRun ? (
          <button
            type="button"
            className="notes-agent-block-run"
            onClick={() => onRun(props.node.textContent)}
            title={t("notes.agent.runHint", {
              defaultValue: "Send this task to the chat",
            })}
          >
            <Play className="h-3 w-3" aria-hidden />
            {t("notes.agent.run", { defaultValue: "Run" })}
          </button>
        ) : null}
      </div>
      <pre>
        <NodeViewContent<"code"> as="code" />
      </pre>
    </NodeViewWrapper>
  );
}

const SLASH_ICONS: Record<SlashCommandId, typeof Heading1> = {
  title: Heading1,
  h1: Heading1,
  h2: Heading2,
  h3: Heading3,
  text: Pilcrow,
  todo: CheckSquare,
  bullet: List,
  numbered: ListOrdered,
  quote: Quote,
  code: Code2,
  table: Table2,
  divider: Minus,
  link: Link2,
  image: ImagePlus,
  file: FileUp,
  agent: Bot,
};

interface SlashState {
  query: string;
  /** Doc range covering "/query", deleted when a command is picked. */
  from: number;
  to: number;
  top: number;
  left: number;
  selected: number;
}

type WikilinkState = SlashState;

export interface NoteEditorProps {
  token: string;
  /** Note id; the editor resets when it changes. */
  noteId: string;
  markdown: string;
  /** Called (debounced upstream work is done here) whenever content changed. */
  onMarkdownChange: (markdown: string) => void;
  readOnly?: boolean;
  /** Run an /agent block: receives the raw block text. */
  onRunAgent?: (spec: string) => void;
  /** Preview an "_files/…" attachment in-product (no browser tab, ever). */
  onOpenAttachment?: (path: string, name: string) => void;
  wikilinkTargets?: WikilinkTarget[];
  onOpenWikilink?: (title: string) => void;
}

/** Live editor snapshot for export (markdown + rich HTML with colours). */
export interface NoteEditorHandle {
  getSnapshot: () => { markdown: string; html: string } | null;
  navigateToText: (text: string) => void;
}

export const NoteEditor = forwardRef<NoteEditorHandle, NoteEditorProps>(function NoteEditor(
  {
    token,
    noteId,
    markdown,
    onMarkdownChange,
    readOnly = false,
    onRunAgent,
    onOpenAttachment,
    wikilinkTargets = [],
    onOpenWikilink,
  },
  ref,
) {
  const { t } = useTranslation();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const imageInputRef = useRef<HTMLInputElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [slash, setSlash] = useState<SlashState | null>(null);
  const slashRef = useRef<SlashState | null>(null);
  slashRef.current = slash;
  const [wikilink, setWikilink] = useState<WikilinkState | null>(null);
  const wikilinkRef = useRef<WikilinkState | null>(null);
  wikilinkRef.current = wikilink;
  const wikilinkTargetsRef = useRef(wikilinkTargets);
  wikilinkTargetsRef.current = wikilinkTargets;
  /** Floating format bar shown over a non-empty text selection. */
  const [selBar, setSelBar] = useState<{ top: number; left: number } | null>(null);
  /** In-app link dialog (never window.prompt: broken in packaged builds). */
  const [linkDraft, setLinkDraft] = useState<
    { url: string; text: string; fromSelection: boolean } | null
  >(null);
  // editorProps lives inside useEditor (deps [noteId]); refs keep the DOM
  // handlers pointed at the current callbacks instead of stale ones.
  const openAttachmentRef = useRef(onOpenAttachment);
  openAttachmentRef.current = onOpenAttachment;
  const openWikilinkRef = useRef(onOpenWikilink);
  openWikilinkRef.current = onOpenWikilink;
  // Packaged desktop (Tauri WebView) blocks window.open / target=_blank;
  // the gateway opens the OS browser. Plain browser tabs keep the popup path.
  const openExternalRef = useRef<(url: string) => void>(() => {});
  openExternalRef.current = (url: string) => {
    const fallback = () => {
      window.open(url, "_blank", "noopener,noreferrer");
    };
    void openExternalUrl(token, url)
      .then((result) => {
        if (!result.opened) fallback();
      })
      .catch(fallback);
  };
  const insertUploadRef = useRef<
    ((file: File, as: "image" | "file") => Promise<void>) | null
  >(null);

  const uploadIncomingFiles = useCallback((files: FileList | File[]) => {
    const upload = insertUploadRef.current;
    if (!upload) return false;
    const list = [...files].filter((file) => file.size > 0);
    if (!list.length) return false;
    void (async () => {
      for (const file of list) {
        await upload(file, file.type.startsWith("image/") ? "image" : "file");
      }
    })();
    return true;
  }, []);

  const extensions = useMemo(() => {
    return [
      StarterKit.configure({
        codeBlock: false,
        link: { openOnClick: false, autolink: true },
      }),
      CodeBlockLowlight.extend({
        addNodeView() {
          return ReactNodeViewRenderer(CodeBlockView);
        },
      }).configure({ lowlight }),
      TaskList,
      TaskItem.configure({ nested: true }),
      Table.configure({ resizable: false }),
      TableRow,
      TableHeader,
      TableCell,
      Highlight.configure({ multicolor: true }),
      TextStyle,
      Color,
      Image.extend({
        // Markdown keeps the relative "_files/…" path (portable, no token in
        // the note); only the rendered <img> gets the authenticated URL. The
        // original path rides along as a data attribute so a click can open
        // the in-product preview.
        renderHTML({ HTMLAttributes }) {
          const src = String(HTMLAttributes.src ?? "");
          if (!src.startsWith("_files/")) {
            return ["img", HTMLAttributes];
          }
          return [
            "img",
            {
              ...HTMLAttributes,
              src: noteAttachmentUrl(token, src),
              "data-navin-file": src,
            },
          ];
        },
        // A paste must go through uploadIncomingFiles. TipTap would otherwise
        // also insert the clipboard bytes as a second inline image next to
        // the uploaded `_files/` one.
      }).configure({ allowBase64: false }),
      Placeholder.configure({
        placeholder: t("notes.editor.placeholder", {
          defaultValue: "Write, or type / for blocks…",
        }),
      }),
      // html: true is what lets colour marks (highlight, text colour) survive
      // the markdown round-trip as inline HTML; parsing goes through
      // DOMParser + the Tiptap schema, so scripts and unknown tags are dropped.
      Markdown.configure({
        html: true,
        linkify: true,
        transformPastedText: true,
      }),
      createWikilinkExtension((title) => openWikilinkRef.current?.(title)),
    ];
  }, [t, token]);

  const updateSlashFromEditor = useCallback((editor: Editor) => {
    const { $from, empty } = editor.state.selection;
    if (!empty || $from.parent.type.name !== "paragraph") {
      setSlash(null);
      return;
    }
    const blockText = $from.parent.textContent;
    const query = slashQueryFromBlockText(blockText);
    // The cursor must sit at the end of the "/query" text.
    if (query === null || $from.parentOffset !== blockText.length) {
      setSlash(null);
      return;
    }
    const blockStart = $from.start();
    const coords = editor.view.coordsAtPos(blockStart);
    const box = containerRef.current?.getBoundingClientRect();
    setSlash((previous) => ({
      query,
      from: blockStart,
      to: blockStart + blockText.length,
      top: coords.bottom - (box?.top ?? 0) + 6,
      left: Math.min(coords.left - (box?.left ?? 0), (box?.width ?? 600) - 280),
      selected:
        previous && previous.query === query ? previous.selected : 0,
    }));
  }, []);

  const updateSelectionBar = useCallback((editor: Editor) => {
    if (editor.isDestroyed || !editor.isEditable) {
      setSelBar(null);
      return;
    }
    const { empty, from, to } = editor.state.selection;
    if (empty || editor.isActive("codeBlock")) {
      setSelBar(null);
      return;
    }
    const text = editor.state.doc.textBetween(from, to, " ").trim();
    if (!text) {
      setSelBar(null);
      return;
    }
    const coords = editor.view.coordsAtPos(from);
    const box = containerRef.current?.getBoundingClientRect();
    setSelBar({
      top: Math.max(coords.top - (box?.top ?? 0) - 42, 4),
      left: Math.min(
        Math.max(coords.left - (box?.left ?? 0), 8),
        Math.max((box?.width ?? 600) - 360, 8),
      ),
    });
  }, []);

  const updateWikilinkFromEditor = useCallback((editor: Editor) => {
    const { $from, empty } = editor.state.selection;
    if (!empty || $from.parent.type.name !== "paragraph") {
      setWikilink(null);
      return;
    }
    const query = wikilinkQueryAt($from.parent.textContent, $from.parentOffset);
    if (!query) {
      setWikilink(null);
      return;
    }
    const blockStart = $from.start();
    const coords = editor.view.coordsAtPos($from.pos);
    const box = containerRef.current?.getBoundingClientRect();
    setWikilink((previous) => ({
      query: query.query,
      from: blockStart + query.from,
      to: blockStart + query.to,
      top: coords.bottom - (box?.top ?? 0) + 6,
      left: Math.min(coords.left - (box?.left ?? 0), (box?.width ?? 600) - 300),
      selected:
        previous && previous.query === query.query ? previous.selected : 0,
    }));
  }, []);

  const editor = useEditor(
    {
      extensions,
      content: markdown,
      editable: !readOnly,
      onUpdate: ({ editor: current }) => {
        const value = markdownOf(current);
        if (value !== null) onMarkdownChange(value);
        updateSlashFromEditor(current);
        updateWikilinkFromEditor(current);
        updateSelectionBar(current);
      },
      onSelectionUpdate: ({ editor: current }) => {
        updateSlashFromEditor(current);
        updateWikilinkFromEditor(current);
        updateSelectionBar(current);
      },
      editorProps: {
        // Attachment links ("_files/…") and images open the in-product
        // preview. Letting the browser follow the raw URL would leak the
        // token into a tab, and packaged desktop builds have no tabs at all.
        handleDOMEvents: {
          click: (_view, event) => {
            const element = event.target as HTMLElement | null;
            const anchor = element?.closest?.("a");
            const href = anchor?.getAttribute("href") ?? "";
            // Ctrl/Cmd+click opens web links via the gateway (OS browser on
            // Tauri); a plain click keeps the caret for editing.
            if (/^https?:\/\//i.test(href) && (event.ctrlKey || event.metaKey)) {
              event.preventDefault();
              openExternalRef.current(href);
              return true;
            }
            const open = openAttachmentRef.current;
            if (!open) return false;
            if (href.startsWith("_files/")) {
              event.preventDefault();
              open(href, anchor?.textContent?.trim() || href.slice("_files/".length));
              return true;
            }
            const image = element?.closest?.("img");
            const path = image?.getAttribute("data-navin-file") ?? "";
            if (path) {
              event.preventDefault();
              open(path, image?.getAttribute("alt") || path.slice("_files/".length));
              return true;
            }
            return false;
          },
          // Files dropped or pasted into the note upload to the store and
          // insert an image or a link at the caret, same as /image and /file.
          drop: (_view, event) => {
            const files = extractImageFilesFromDrop(event);
            if (!files.length) return false;
            if (uploadIncomingFiles(files)) {
              event.preventDefault();
              return true;
            }
            return false;
          },
          paste: (_view, event) => {
            const files = extractImageFilesFromPaste(event);
            if (!files.length) return false;
            if (uploadIncomingFiles(files)) {
              event.preventDefault();
              return true;
            }
            return false;
          },
        },
        handleKeyDown: (_view, event) => {
          const currentWikilink = wikilinkRef.current;
          if (currentWikilink) {
            const targets = filterWikilinkTargets(
              wikilinkTargetsRef.current,
              currentWikilink.query,
            );
            if (event.key === "Escape") {
              setWikilink(null);
              return true;
            }
            if (targets.length && event.key === "ArrowDown") {
              event.preventDefault();
              setWikilink({
                ...currentWikilink,
                selected: (currentWikilink.selected + 1) % targets.length,
              });
              return true;
            }
            if (targets.length && event.key === "ArrowUp") {
              event.preventDefault();
              setWikilink({
                ...currentWikilink,
                selected:
                  (currentWikilink.selected - 1 + targets.length) % targets.length,
              });
              return true;
            }
            if (targets.length && (event.key === "Enter" || event.key === "Tab")) {
              event.preventDefault();
              const target =
                targets[Math.min(currentWikilink.selected, targets.length - 1)];
              if (target) {
                _view.dispatch(
                  _view.state.tr.insertText(
                    `[[${target.title}]]`,
                    currentWikilink.from,
                    currentWikilink.to,
                  ),
                );
                setWikilink(null);
                return true;
              }
            }
          }
          const current = slashRef.current;
          if (!current) return false;
          const items = filterSlashCommands(current.query);
          if (event.key === "Escape") {
            setSlash(null);
            return true;
          }
          if (!items.length) return false;
          if (event.key === "ArrowDown") {
            setSlash({
              ...current,
              selected: (current.selected + 1) % items.length,
            });
            return true;
          }
          if (event.key === "ArrowUp") {
            setSlash({
              ...current,
              selected: (current.selected - 1 + items.length) % items.length,
            });
            return true;
          }
          if (event.key === "Enter" || event.key === "Tab") {
            const item = items[Math.min(current.selected, items.length - 1)];
            if (item) {
              event.preventDefault();
              runSlashCommandRef.current?.(item);
              return true;
            }
          }
          return false;
        },
      },
    },
    [noteId],
  );

  // Apply external content changes (switching notes, restore from history)
  // without recreating the editor mid-typing: only reset when the incoming
  // markdown differs from what the editor already holds.
  useEffect(() => {
    if (!editor || editor.isDestroyed) return;
    const current = markdownOf(editor);
    if (current !== null && current !== markdown) {
      editor.commands.setContent(markdown);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editor, noteId]);

  useEffect(() => {
    if (!editor || editor.isDestroyed) return;
    editor.setEditable(!readOnly);
  }, [editor, readOnly]);

  useImperativeHandle(
    ref,
    () => ({
      getSnapshot: () => {
        if (!editor || editor.isDestroyed) return null;
        const value = markdownOf(editor);
        return {
          markdown: value ?? "",
          html: editor.getHTML(),
        };
      },
      navigateToText: (text: string) => {
        if (!editor || editor.isDestroyed || !text.trim()) return;
        const needle = text.trim();
        let match: { from: number; to: number } | null = null;
        editor.state.doc.descendants((node, position) => {
          if (match || !node.isText || !node.text) return;
          const index = node.text.toLocaleLowerCase().indexOf(needle.toLocaleLowerCase());
          if (index >= 0) {
            match = { from: position + index, to: position + index + needle.length };
          }
        });
        if (match) {
          editor.chain().focus().setTextSelection(match).scrollIntoView().run();
        }
      },
    }),
    [editor],
  );

  const insertUpload = useCallback(
    async (file: File, as: "image" | "file") => {
      if (!editor) return;
      const data = await file.arrayBuffer();
      let binary = "";
      const bytes = new Uint8Array(data);
      const chunk = 0x8000;
      for (let i = 0; i < bytes.length; i += chunk) {
        binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
      }
      const saved = await uploadNoteAttachment(token, file.name, btoa(binary));
      if (editor.isDestroyed) return;
      if (as === "image") {
        editor.chain().focus().setImage({ src: saved.path, alt: saved.name }).run();
      } else {
        editor
          .chain()
          .focus()
          .insertContent({
            type: "text",
            text: saved.name,
            marks: [{ type: "link", attrs: { href: saved.path } }],
          })
          .run();
      }
    },
    [editor, token],
  );
  insertUploadRef.current = insertUpload;

  const runSlashCommand = useCallback(
    (item: SlashCommandItem) => {
      const state = slashRef.current;
      if (!editor || !state) return;
      const chain = editor
        .chain()
        .focus()
        .deleteRange({ from: state.from, to: state.to });
      switch (item.id) {
        case "title":
        case "h1":
          chain.setHeading({ level: 1 }).run();
          break;
        case "h2":
          chain.setHeading({ level: 2 }).run();
          break;
        case "h3":
          chain.setHeading({ level: 3 }).run();
          break;
        case "text":
          chain.setParagraph().run();
          break;
        case "todo":
          chain.toggleTaskList().run();
          break;
        case "bullet":
          chain.toggleBulletList().run();
          break;
        case "numbered":
          chain.toggleOrderedList().run();
          break;
        case "quote":
          chain.toggleBlockquote().run();
          break;
        case "code":
          chain.toggleCodeBlock().run();
          break;
        case "table":
          chain.insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run();
          break;
        case "divider":
          chain.setHorizontalRule().run();
          break;
        case "link":
          chain.run();
          setLinkDraft({ url: "", text: "", fromSelection: false });
          break;
        case "image":
          chain.run();
          imageInputRef.current?.click();
          break;
        case "file":
          chain.run();
          fileInputRef.current?.click();
          break;
        case "agent":
          chain
            .insertContent({
              type: "codeBlock",
              attrs: { language: AGENT_BLOCK_LANGUAGE },
              content: [
                {
                  type: "text",
                  text: agentBlockTemplate(
                    t("notes.agent.templateObjective", {
                      defaultValue: "describe what the agent must do",
                    }),
                    t("notes.agent.templateOutput", {
                      defaultValue: "this note",
                    }),
                  ),
                },
              ],
            })
            .run();
          break;
      }
      setSlash(null);
    },
    [editor, t],
  );
  const runSlashCommandRef = useRef(runSlashCommand);
  runSlashCommandRef.current = runSlashCommand;

  const openLinkDialogForSelection = useCallback(() => {
    if (!editor || editor.isDestroyed) return;
    const { from, to } = editor.state.selection;
    const text = editor.state.doc.textBetween(from, to, " ");
    const existing = (editor.getAttributes("link").href as string | undefined) ?? "";
    setLinkDraft({ url: existing, text, fromSelection: true });
  }, [editor]);

  const applyLinkDraft = useCallback(() => {
    if (!editor || editor.isDestroyed || !linkDraft) return;
    const href = normalizeHref(linkDraft.url);
    if (!href) {
      setLinkDraft(null);
      return;
    }
    if (linkDraft.fromSelection) {
      editor.chain().focus().extendMarkRange("link").setLink({ href }).run();
    } else {
      const label = linkDraft.text.trim() || href;
      editor
        .chain()
        .focus()
        .insertContent([
          { type: "text", text: label, marks: [{ type: "link", attrs: { href } }] },
          { type: "text", text: " " },
        ])
        .run();
    }
    setLinkDraft(null);
  }, [editor, linkDraft]);

  const slashItems = slash ? filterSlashCommands(slash.query) : [];
  const wikilinkItems = wikilink
    ? filterWikilinkTargets(wikilinkTargets, wikilink.query)
    : [];

  return (
    <div ref={containerRef} className="notes-editor relative h-full min-h-0">
      <AgentRunContext.Provider value={onRunAgent ?? null}>
        <EditorContent
          editor={editor}
          className="h-full overflow-y-auto px-4 py-6 sm:px-8"
        />
      </AgentRunContext.Provider>

      <input
        ref={imageInputRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) void insertUpload(file, "image");
          event.target.value = "";
        }}
      />
      <input
        ref={fileInputRef}
        type="file"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) void insertUpload(file, "file");
          event.target.value = "";
        }}
      />

      <AnimatePresence>
        {selBar && editor && !editor.isDestroyed ? (
          <motion.div
            key="selection-bar"
            initial={{ opacity: 0, y: 4, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 4, scale: 0.97 }}
            transition={{ duration: 0.1, ease: "easeOut" }}
            className="absolute z-40 flex items-center gap-0.5 rounded-lg border border-border/70 bg-popover p-1 shadow-xl"
            style={{ top: selBar.top, left: selBar.left }}
            role="toolbar"
            aria-label={t("notes.formatBar.label", { defaultValue: "Format selection" })}
          >
            <BarButton
              label={t("notes.formatBar.bold", { defaultValue: "Bold" })}
              active={editor.isActive("bold")}
              onPress={() => editor.chain().focus().toggleBold().run()}
            >
              <Bold className="h-3.5 w-3.5" aria-hidden />
            </BarButton>
            <BarButton
              label={t("notes.formatBar.italic", { defaultValue: "Italic" })}
              active={editor.isActive("italic")}
              onPress={() => editor.chain().focus().toggleItalic().run()}
            >
              <Italic className="h-3.5 w-3.5" aria-hidden />
            </BarButton>
            <BarButton
              label={t("notes.formatBar.link", { defaultValue: "Link" })}
              active={editor.isActive("link")}
              onPress={openLinkDialogForSelection}
            >
              <Link2 className="h-3.5 w-3.5" aria-hidden />
            </BarButton>
            <span className="mx-0.5 h-4 w-px bg-border/70" aria-hidden />
            {TEXT_COLORS.map((color) => (
              <BarButton
                key={`text-${color.id}`}
                label={t("notes.formatBar.textColor", {
                  defaultValue: "Text colour {{name}}",
                  name: color.id,
                })}
                active={editor.isActive("textStyle", { color: color.value })}
                onPress={() => editor.chain().focus().setColor(color.value).run()}
              >
                <span className="text-[12px] font-bold" style={{ color: color.value }}>
                  A
                </span>
              </BarButton>
            ))}
            <span className="mx-0.5 h-4 w-px bg-border/70" aria-hidden />
            {HIGHLIGHT_COLORS.map((color) => (
              <BarButton
                key={`hl-${color.id}`}
                label={t("notes.formatBar.highlight", {
                  defaultValue: "Highlight {{name}}",
                  name: color.id,
                })}
                active={editor.isActive("highlight", { color: color.value })}
                onPress={() =>
                  editor.chain().focus().toggleHighlight({ color: color.value }).run()
                }
              >
                <span
                  className="flex h-3.5 w-3.5 items-center justify-center rounded-[4px]"
                  style={{ backgroundColor: color.value }}
                >
                  <Highlighter className="h-2.5 w-2.5 opacity-70" aria-hidden />
                </span>
              </BarButton>
            ))}
            <span className="mx-0.5 h-4 w-px bg-border/70" aria-hidden />
            <BarButton
              label={t("notes.formatBar.clear", { defaultValue: "Clear colours" })}
              onPress={() =>
                editor.chain().focus().unsetColor().unsetHighlight().run()
              }
            >
              <Eraser className="h-3.5 w-3.5" aria-hidden />
            </BarButton>
          </motion.div>
        ) : null}
      </AnimatePresence>

      <AnimatePresence>
        {linkDraft ? (
          <motion.div
            key="link-dialog"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.12 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
            onMouseDown={(event) => {
              if (event.target === event.currentTarget) setLinkDraft(null);
            }}
            role="dialog"
            aria-modal="true"
            aria-label={t("notes.link.title", { defaultValue: "Insert link" })}
          >
            <motion.div
              initial={{ opacity: 0, scale: 0.97, y: 6 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.97, y: 6 }}
              transition={{ duration: 0.14, ease: "easeOut" }}
              className="w-full max-w-sm rounded-2xl border border-border/70 bg-background p-4 shadow-2xl"
              onKeyDown={(event) => {
                if (event.key === "Escape") setLinkDraft(null);
                if (event.key === "Enter") {
                  event.preventDefault();
                  applyLinkDraft();
                }
              }}
            >
              <p className="mb-3 flex items-center gap-1.5 text-[13px] font-semibold">
                <Link2 className="h-3.5 w-3.5" aria-hidden />
                {t("notes.link.title", { defaultValue: "Insert link" })}
              </p>
              {!linkDraft.fromSelection ? (
                <input
                  value={linkDraft.text}
                  onChange={(event) =>
                    setLinkDraft({ ...linkDraft, text: event.target.value })
                  }
                  placeholder={t("notes.link.textPlaceholder", {
                    defaultValue: "Link text",
                  })}
                  className="mb-2 h-9 w-full rounded-lg border border-border/70 bg-background px-3 text-[13px] outline-none transition-colors focus:border-primary/50"
                />
              ) : null}
              <input
                value={linkDraft.url}
                onChange={(event) =>
                  setLinkDraft({ ...linkDraft, url: event.target.value })
                }
                placeholder="https://…"
                autoFocus
                className="h-9 w-full rounded-lg border border-border/70 bg-background px-3 font-mono text-[12.5px] outline-none transition-colors focus:border-primary/50"
              />
              <div className="mt-3 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setLinkDraft(null)}
                  className="h-8 rounded-lg px-3 text-[12.5px] text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
                >
                  {t("notes.link.cancel", { defaultValue: "Cancel" })}
                </button>
                <button
                  type="button"
                  onClick={applyLinkDraft}
                  disabled={!linkDraft.url.trim()}
                  className="h-8 rounded-lg bg-foreground px-3 text-[12.5px] font-medium text-background transition-opacity hover:opacity-85 disabled:opacity-50"
                >
                  {t("notes.link.insert", { defaultValue: "Insert" })}
                </button>
              </div>
            </motion.div>
          </motion.div>
        ) : null}
      </AnimatePresence>

      <AnimatePresence>
        {wikilink && wikilinkItems.length > 0 ? (
          <motion.div
            key="wikilink-menu"
            initial={{ opacity: 0, scale: 0.97, y: -4 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.97, y: -4 }}
            transition={{ duration: 0.12, ease: "easeOut" }}
            className="absolute z-40 w-72 overflow-hidden rounded-xl border border-border/70 bg-popover p-1 shadow-xl"
            style={{ top: wikilink.top, left: Math.max(wikilink.left, 8) }}
            role="listbox"
            aria-label={t("notes.wikilink.menu", { defaultValue: "Link a note" })}
          >
            {wikilinkItems.map((target, index) => (
              <button
                key={target.id}
                type="button"
                role="option"
                aria-selected={index === wikilink.selected}
                onMouseEnter={() => setWikilink({ ...wikilink, selected: index })}
                onMouseDown={(event) => {
                  event.preventDefault();
                  if (!editor) return;
                  editor
                    .chain()
                    .focus()
                    .insertContentAt(
                      { from: wikilink.from, to: wikilink.to },
                      `[[${target.title}]]`,
                    )
                    .run();
                  setWikilink(null);
                }}
                className={cn(
                  "block w-full rounded-lg px-2.5 py-2 text-left outline-none",
                  index === wikilink.selected ? "bg-muted" : "hover:bg-muted/60",
                )}
              >
                <span className="block truncate text-[13px] font-medium">
                  {target.title}
                </span>
                {target.aliases.length ? (
                  <span className="block truncate text-[11px] text-muted-foreground">
                    {target.aliases.join(", ")}
                  </span>
                ) : null}
              </button>
            ))}
          </motion.div>
        ) : null}
      </AnimatePresence>

      <AnimatePresence>
        {slash && slashItems.length > 0 ? (
          <motion.div
            key="slash-menu"
            initial={{ opacity: 0, scale: 0.96, y: -4 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.97, y: -4 }}
            transition={{ duration: 0.12, ease: "easeOut" }}
            className="absolute z-40 w-72 overflow-hidden rounded-xl border border-border/70 bg-popover shadow-xl"
            style={{ top: slash.top, left: Math.max(slash.left, 8) }}
            role="listbox"
            aria-label={t("notes.slash.menuLabel", { defaultValue: "Insert block" })}
          >
            <div className="max-h-72 overflow-y-auto p-1">
              {slashItems.map((item, index) => {
                const Icon = SLASH_ICONS[item.id];
                const active = index === slash.selected;
                return (
                  <button
                    key={item.id}
                    type="button"
                    role="option"
                    aria-selected={active}
                    onMouseEnter={() => setSlash({ ...slash, selected: index })}
                    onMouseDown={(event) => {
                      event.preventDefault();
                      runSlashCommand(item);
                    }}
                    className={cn(
                      "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-[13px] transition-colors",
                      active ? "bg-muted text-foreground" : "text-foreground/85",
                    )}
                  >
                    <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-border/60 bg-background">
                      <Icon className="h-3.5 w-3.5" aria-hidden />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-medium">
                        {t(item.labelKey, { defaultValue: item.defaultLabel })}
                      </span>
                      <span className="block truncate text-[11px] text-muted-foreground">
                        {t(`${item.labelKey}Hint`, { defaultValue: item.defaultHint })}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
});
