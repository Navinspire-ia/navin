import { describe, expect, it } from "vitest";

import {
  SLASH_COMMANDS,
  filterSlashCommands,
  slashQueryFromBlockText,
} from "./slash-commands";

describe("slashQueryFromBlockText", () => {
  it("opens on a lone slash with everything listed", () => {
    expect(slashQueryFromBlockText("/")).toBe("");
    expect(filterSlashCommands("")).toHaveLength(SLASH_COMMANDS.length);
  });

  it("captures the text typed after the slash", () => {
    expect(slashQueryFromBlockText("/tab")).toBe("tab");
  });

  it("stays closed for ordinary text and long or path-like input", () => {
    expect(slashQueryFromBlockText("hello")).toBeNull();
    expect(slashQueryFromBlockText("/src/components/file")).toBeNull();
    expect(slashQueryFromBlockText("/" + "x".repeat(30))).toBeNull();
  });
});

describe("filterSlashCommands", () => {
  it("matches ids, labels and keywords", () => {
    expect(filterSlashCommands("table").map((i) => i.id)).toContain("table");
    expect(filterSlashCommands("todo").map((i) => i.id)).toContain("todo");
    expect(filterSlashCommands("h2").map((i) => i.id)).toContain("h2");
  });

  it("matches French synonyms, accent-insensitively", () => {
    expect(filterSlashCommands("tâche").map((i) => i.id)).toContain("todo");
    expect(filterSlashCommands("numéroté").map((i) => i.id)).toContain(
      "numbered",
    );
    expect(filterSlashCommands("séparateur").map((i) => i.id)).toContain(
      "divider",
    );
  });

  it("returns nothing for gibberish so the menu closes", () => {
    expect(filterSlashCommands("zzzz")).toHaveLength(0);
  });

  it("every command declares an icon-facing id exactly once", () => {
    const ids = SLASH_COMMANDS.map((item) => item.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});
