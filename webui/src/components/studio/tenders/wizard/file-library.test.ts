import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { FileLibrary } from "@/components/studio/tenders/wizard/WizardChrome";

const tx = (_key: string, fallback: string) => fallback;

describe("tenders file preview", () => {
  it("shows the extract when the upload stored an excerpt", () => {
    const html = renderToStaticMarkup(
      createElement(FileLibrary, {
        icon: "TextDocument",
        title: "Models",
        files: [{ file_id: "f1", name: "house.docx", excerpt: "Cover page then pricing." }],
        emptyLabel: "None",
        action: "Import",
        onImport: () => {},
        onRemove: () => {},
        tx,
      }),
    );
    expect(html).toContain("Preview extract");
    expect(html).toContain("Cover page then pricing.");
    expect(html).not.toContain("No text extracted yet.");
  });

  it("offers action=file when an id is on file without an excerpt", () => {
    const html = renderToStaticMarkup(
      createElement(FileLibrary, {
        icon: "TextDocument",
        title: "Models",
        files: [{ file_id: "f3", name: "later.docx" }],
        emptyLabel: "None",
        action: "Import",
        onImport: () => {},
        onRemove: () => {},
        onPreview: async () => "Full extract from disk.",
        tx,
      }),
    );
    expect(html).toContain("Preview extract");
    expect(html).not.toContain("No text extracted yet.");
  });

  it("says when no extract is on file", () => {
    const html = renderToStaticMarkup(
      createElement(FileLibrary, {
        icon: "TextDocument",
        title: "Models",
        files: [{ file_id: "f2", name: "empty.docx" }],
        emptyLabel: "None",
        action: "Import",
        onImport: () => {},
        onRemove: () => {},
        tx,
      }),
    );
    expect(html).toContain("No text extracted yet.");
    expect(html).not.toContain("Preview extract");
  });
});
