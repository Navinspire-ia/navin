// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { NotesSearchResults } from "./NotesAdvancedPanes";

describe("NotesSearchResults", () => {
  it("renders snippets, line numbers and highlighted matches", () => {
    const html = renderToStaticMarkup(
      createElement(NotesSearchResults, {
        query: "juillet",
        loading: false,
        onOpen: () => undefined,
        results: [
          {
            id: "note-1",
            title: "Décisions",
            folder: "projet",
            title_match: false,
            snippet: "Décision de juillet",
            matches: [{ line: 7, text: "Décision de juillet", col: 12 }],
          },
        ],
      }),
    );
    expect(html).toContain("<mark");
    expect(html).toContain("juillet");
    expect(html).toContain("L7");
  });
});
