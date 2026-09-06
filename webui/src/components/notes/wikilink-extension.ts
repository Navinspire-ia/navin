import { Extension } from "@tiptap/core";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";

export interface WikilinkTarget {
  id: string;
  title: string;
  aliases: string[];
}

export interface WikilinkQuery {
  query: string;
  from: number;
  to: number;
}

const WIKILINK_RE = /\[\[([^[\]\n|]+)(?:\|([^[\]\n]+))?\]\]/g;

export function wikilinkQueryAt(text: string, cursorOffset: number): WikilinkQuery | null {
  const before = text.slice(0, cursorOffset);
  const start = before.lastIndexOf("[[");
  if (start < 0) return null;
  const query = before.slice(start + 2);
  if (query.includes("]]") || query.includes("\n") || query.includes("|")) return null;
  return { query, from: start, to: cursorOffset };
}

export function filterWikilinkTargets(
  targets: WikilinkTarget[],
  query: string,
  limit = 8,
): WikilinkTarget[] {
  const needle = query.trim().toLocaleLowerCase();
  return targets
    .filter(
      (target) =>
        !needle ||
        target.title.toLocaleLowerCase().includes(needle) ||
        target.aliases.some((alias) => alias.toLocaleLowerCase().includes(needle)),
    )
    .slice(0, limit);
}

export function createWikilinkExtension(onOpen: (title: string) => void) {
  return Extension.create({
    name: "navinWikilink",
    addProseMirrorPlugins() {
      return [
        new Plugin({
          key: new PluginKey("navinWikilink"),
          props: {
            decorations(state) {
              const decorations: Decoration[] = [];
              state.doc.descendants((node, position) => {
                if (!node.isText || !node.text) return;
                for (const match of node.text.matchAll(WIKILINK_RE)) {
                  const offset = match.index ?? 0;
                  const title = match[1].trim();
                  decorations.push(
                    Decoration.inline(
                      position + offset,
                      position + offset + match[0].length,
                      {
                        class: "notes-wikilink",
                        "data-wikilink-title": title,
                        role: "link",
                        tabindex: "0",
                      },
                    ),
                  );
                }
              });
              return DecorationSet.create(state.doc, decorations);
            },
            handleClick(_view, _position, event) {
              const element = event.target as HTMLElement | null;
              const link = element?.closest?.("[data-wikilink-title]");
              const title = link?.getAttribute("data-wikilink-title");
              if (!title) return false;
              event.preventDefault();
              onOpen(title);
              return true;
            },
            handleKeyDown(_view, event) {
              if (event.key !== "Enter") return false;
              const element = event.target as HTMLElement | null;
              const link = element?.closest?.("[data-wikilink-title]");
              const title = link?.getAttribute("data-wikilink-title");
              if (!title) return false;
              event.preventDefault();
              onOpen(title);
              return true;
            },
          },
        }),
      ];
    },
  });
}
