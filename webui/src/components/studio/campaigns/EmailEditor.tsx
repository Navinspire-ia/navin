// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useMemo, useRef, useState } from "react";
import { Extension } from "@tiptap/core";
import { EditorContent, useEditor, type Editor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Highlight from "@tiptap/extension-highlight";
import Image from "@tiptap/extension-image";
import Link from "@tiptap/extension-link";
import Placeholder from "@tiptap/extension-placeholder";
import { Table, TableCell, TableHeader, TableRow } from "@tiptap/extension-table";
import { Color, TextStyle } from "@tiptap/extension-text-style";
import Underline from "@tiptap/extension-underline";
import {
  AlignCenter,
  AlignLeft,
  AlignRight,
  Bold,
  Braces,
  Heading1,
  Heading2,
  Highlighter,
  ImagePlus,
  Italic,
  Link2,
  List,
  ListOrdered,
  Minus,
  MousePointerClick,
  Quote,
  Redo2,
  Strikethrough,
  Table as TableIcon,
  Underline as UnderlineIcon,
  Undo2,
  Unlink,
} from "lucide-react";

import { CAMPAIGN_MERGE_TAGS } from "@/lib/campaigns-types";
import { cn } from "@/lib/utils";
import type { Tx } from "./campaigns-ui";
import "./campaigns.css";

/** Paragraph / heading alignment as an inline style (email clients ignore classes). */
const TextAlign = Extension.create({
  name: "emailTextAlign",
  addGlobalAttributes() {
    return [
      {
        types: ["heading", "paragraph"],
        attributes: {
          textAlign: {
            default: null,
            parseHTML: (element: HTMLElement) => element.style.textAlign || null,
            renderHTML: (attributes: Record<string, unknown>) =>
              attributes.textAlign ? { style: `text-align: ${String(attributes.textAlign)}` } : {},
          },
        },
      },
    ];
  },
});

/** Links keep an inline style, which is how "buttons" survive email clients. */
const StyledLink = Link.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      style: {
        default: null,
        parseHTML: (element: HTMLElement) => element.getAttribute("style"),
        renderHTML: (attributes: Record<string, unknown>) =>
          attributes.style ? { style: String(attributes.style) } : {},
      },
    };
  },
});

export const BUTTON_STYLE =
  "display:inline-block;padding:12px 22px;border-radius:8px;background:#1d6af0;color:#ffffff;text-decoration:none;font-weight:600;";

const COLORS = ["#0f172a", "#1d6af0", "#1f9d74", "#c2410c", "#d4892a", "#6d5bd0", "#be185d", "#64748b"];

export function mergeTag(name: string, fallback = ""): string {
  return fallback ? `{{${name}|${fallback}}}` : `{{${name}}}`;
}

export function EmailEditor({
  value,
  onChange,
  tx,
  extraTags = [],
  placeholder,
}: {
  value: string;
  onChange: (html: string) => void;
  tx: Tx;
  /** attrs.* columns found in contacts, offered as merge tags. */
  extraTags?: string[];
  placeholder?: string;
}) {
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const extensions = useMemo(
    () => [
      StarterKit.configure({ link: false, underline: false }),
      Underline,
      StyledLink.configure({ openOnClick: false, autolink: true }),
      Image.configure({ inline: false }),
      TextStyle,
      Color,
      Highlight.configure({ multicolor: true }),
      Table.configure({ resizable: false }),
      TableRow,
      TableHeader,
      TableCell,
      TextAlign,
      Placeholder.configure({ placeholder: placeholder ?? "" }),
    ],
    [placeholder],
  );
  const editor = useEditor({
    extensions,
    content: value || "",
    editorProps: {
      attributes: {
        class:
          "campaign-email-editor prose prose-sm max-w-none min-h-[320px] px-5 py-4 outline-none dark:prose-invert",
        "aria-label": tx("editor.label", "Email body"),
      },
    },
    onUpdate: ({ editor: current }) => onChangeRef.current(current.getHTML()),
  });

  // An outside change (template loaded) replaces the content once.
  useEffect(() => {
    if (!editor || editor.isDestroyed) return;
    if (value !== editor.getHTML()) editor.commands.setContent(value || "", { emitUpdate: false });
  }, [editor, value]);

  return (
    <div className="overflow-hidden rounded-xl border border-border/70 bg-background">
      {editor ? <Toolbar editor={editor} tx={tx} extraTags={extraTags} /> : null}
      <div className="max-h-[560px] overflow-y-auto">
        <EditorContent editor={editor} />
      </div>
    </div>
  );
}

function Toolbar({ editor, tx, extraTags }: { editor: Editor; tx: Tx; extraTags: string[] }) {
  const [menu, setMenu] = useState<"" | "color" | "highlight" | "tags">("");
  const [, force] = useState(0);
  useEffect(() => {
    const update = () => force((n) => n + 1);
    editor.on("selectionUpdate", update);
    editor.on("transaction", update);
    return () => {
      editor.off("selectionUpdate", update);
      editor.off("transaction", update);
    };
  }, [editor]);

  const chain = () => editor.chain().focus();
  const align = (value: "left" | "center" | "right") => {
    chain().updateAttributes("paragraph", { textAlign: value }).updateAttributes("heading", { textAlign: value }).run();
  };
  const addLink = () => {
    const previous = (editor.getAttributes("link").href as string | undefined) ?? "https://";
    const url = window.prompt(tx("editor.linkPrompt", "Link URL"), previous);
    if (url === null) return;
    if (!url.trim()) {
      chain().extendMarkRange("link").unsetLink().run();
      return;
    }
    chain().extendMarkRange("link").setLink({ href: url.trim() }).run();
  };
  const addImage = () => {
    const url = window.prompt(tx("editor.imagePrompt", "Image URL (https://...)"), "https://");
    if (url && /^https?:\/\//i.test(url.trim())) chain().setImage({ src: url.trim() }).run();
  };
  const addButton = () => {
    const label = window.prompt(tx("editor.buttonLabel", "Button text"), tx("editor.buttonDefault", "Learn more"));
    if (!label) return;
    const url = window.prompt(tx("editor.linkPrompt", "Link URL"), "https://");
    if (!url || !url.trim()) return;
    chain()
      .insertContent({
        type: "paragraph",
        attrs: { textAlign: "center" },
        content: [
          {
            type: "text",
            text: label,
            marks: [{ type: "link", attrs: { href: url.trim(), style: BUTTON_STYLE } }],
          },
        ],
      })
      .run();
  };
  const insertTag = (name: string) => {
    const fallback = name === "first_name" ? tx("editor.fallbackThere", "there") : "";
    chain().insertContent(mergeTag(name, fallback)).run();
    setMenu("");
  };

  const button = (
    label: string,
    icon: React.ReactNode,
    onClick: () => void,
    active = false,
    disabled = false,
  ) => (
    <button
      type="button"
      title={label}
      aria-label={label}
      aria-pressed={active || undefined}
      disabled={disabled}
      onMouseDown={(event) => event.preventDefault()}
      onClick={onClick}
      className={cn(
        "inline-flex h-7 w-7 items-center justify-center rounded-md text-foreground/75 transition-colors hover:bg-muted hover:text-foreground disabled:opacity-35",
        active && "bg-foreground/[0.08] text-foreground",
      )}
    >
      {icon}
    </button>
  );
  const sep = <span className="mx-0.5 h-5 w-px bg-border/70" aria-hidden />;
  const icon = "h-3.5 w-3.5";

  return (
    <div className="relative flex flex-wrap items-center gap-0.5 border-b border-border/60 bg-muted/25 px-2 py-1.5" role="toolbar" aria-label={tx("editor.toolbar", "Formatting")}>
      {button(tx("editor.undo", "Undo"), <Undo2 className={icon} />, () => chain().undo().run(), false, !editor.can().undo())}
      {button(tx("editor.redo", "Redo"), <Redo2 className={icon} />, () => chain().redo().run(), false, !editor.can().redo())}
      {sep}
      {button(tx("editor.h1", "Title"), <Heading1 className={icon} />, () => chain().toggleHeading({ level: 1 }).run(), editor.isActive("heading", { level: 1 }))}
      {button(tx("editor.h2", "Subtitle"), <Heading2 className={icon} />, () => chain().toggleHeading({ level: 2 }).run(), editor.isActive("heading", { level: 2 }))}
      {button(tx("editor.bold", "Bold"), <Bold className={icon} />, () => chain().toggleBold().run(), editor.isActive("bold"))}
      {button(tx("editor.italic", "Italic"), <Italic className={icon} />, () => chain().toggleItalic().run(), editor.isActive("italic"))}
      {button(tx("editor.underline", "Underline"), <UnderlineIcon className={icon} />, () => chain().toggleUnderline().run(), editor.isActive("underline"))}
      {button(tx("editor.strike", "Strikethrough"), <Strikethrough className={icon} />, () => chain().toggleStrike().run(), editor.isActive("strike"))}
      {sep}
      <div className="relative">
        {button(tx("editor.color", "Text color"), <span className="h-3.5 w-3.5 rounded-full border border-black/10" style={{ background: (editor.getAttributes("textStyle").color as string) || "#0f172a" }} />, () => setMenu(menu === "color" ? "" : "color"))}
        {menu === "color" ? (
          <Swatches
            onPick={(color) => {
              if (color) chain().setColor(color).run();
              else chain().unsetColor().run();
              setMenu("");
            }}
            resetLabel={tx("editor.reset", "Default")}
          />
        ) : null}
      </div>
      <div className="relative">
        {button(tx("editor.highlight", "Highlight"), <Highlighter className={icon} />, () => setMenu(menu === "highlight" ? "" : "highlight"), editor.isActive("highlight"))}
        {menu === "highlight" ? (
          <Swatches
            light
            onPick={(color) => {
              if (color) chain().setHighlight({ color }).run();
              else chain().unsetHighlight().run();
              setMenu("");
            }}
            resetLabel={tx("editor.reset", "Default")}
          />
        ) : null}
      </div>
      {sep}
      {button(tx("editor.alignLeft", "Align left"), <AlignLeft className={icon} />, () => align("left"))}
      {button(tx("editor.alignCenter", "Center"), <AlignCenter className={icon} />, () => align("center"))}
      {button(tx("editor.alignRight", "Align right"), <AlignRight className={icon} />, () => align("right"))}
      {sep}
      {button(tx("editor.bullets", "Bulleted list"), <List className={icon} />, () => chain().toggleBulletList().run(), editor.isActive("bulletList"))}
      {button(tx("editor.numbers", "Numbered list"), <ListOrdered className={icon} />, () => chain().toggleOrderedList().run(), editor.isActive("orderedList"))}
      {button(tx("editor.quote", "Quote"), <Quote className={icon} />, () => chain().toggleBlockquote().run(), editor.isActive("blockquote"))}
      {button(tx("editor.rule", "Divider"), <Minus className={icon} />, () => chain().setHorizontalRule().run())}
      {sep}
      {button(tx("editor.link", "Link"), <Link2 className={icon} />, addLink, editor.isActive("link"))}
      {button(tx("editor.unlink", "Remove link"), <Unlink className={icon} />, () => chain().extendMarkRange("link").unsetLink().run(), false, !editor.isActive("link"))}
      {button(tx("editor.image", "Image"), <ImagePlus className={icon} />, addImage)}
      {button(tx("editor.button", "Button"), <MousePointerClick className={icon} />, addButton)}
      {button(tx("editor.table", "Table"), <TableIcon className={icon} />, () => chain().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run())}
      {sep}
      <div className="relative">
        <button
          type="button"
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => setMenu(menu === "tags" ? "" : "tags")}
          className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-[12px] font-medium text-foreground/80 hover:bg-muted"
          aria-expanded={menu === "tags"}
        >
          <Braces className={icon} aria-hidden />
          {tx("editor.mergeTags", "Personalize")}
        </button>
        {menu === "tags" ? (
          <div className="absolute end-0 top-8 z-20 max-h-64 w-52 overflow-y-auto rounded-xl border border-border/70 bg-popover p-1 shadow-lg">
            {[...CAMPAIGN_MERGE_TAGS, ...extraTags.map((key) => `attrs.${key}`)].map((name) => (
              <button
                key={name}
                type="button"
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => insertTag(name)}
                className="block w-full rounded-md px-2 py-1.5 text-start font-mono text-[12px] hover:bg-muted"
              >
                {`{{${name}}}`}
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function Swatches({ onPick, resetLabel, light = false }: { onPick: (color: string) => void; resetLabel: string; light?: boolean }) {
  const colors = light ? ["#fef08a", "#bbf7d0", "#bfdbfe", "#fecaca", "#e9d5ff", "#fed7aa"] : COLORS;
  return (
    <div className="absolute start-0 top-8 z-20 flex w-44 flex-wrap gap-1.5 rounded-xl border border-border/70 bg-popover p-2 shadow-lg">
      {colors.map((color) => (
        <button
          key={color}
          type="button"
          aria-label={color}
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => onPick(color)}
          className="h-6 w-6 rounded-full border border-black/10"
          style={{ background: color }}
        />
      ))}
      <button
        type="button"
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => onPick("")}
        className="mt-1 w-full rounded-md px-2 py-1 text-[11.5px] text-muted-foreground hover:bg-muted"
      >
        {resetLabel}
      </button>
    </div>
  );
}

/** Insert a merge tag at the caret of an input (subject, preheader). */
export function insertAtCaret(input: HTMLInputElement | HTMLTextAreaElement | null, current: string, text: string): string {
  if (!input) return `${current}${text}`;
  const start = input.selectionStart ?? current.length;
  const end = input.selectionEnd ?? current.length;
  return `${current.slice(0, start)}${text}${current.slice(end)}`;
}
