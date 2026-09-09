import { describe, expect, it } from "vitest";

import { studioSessionQuery } from "@/lib/studio-request";

describe("desk request scope", () => {
  it("preserves an encoded chat key without including the selected record", () => {
    const suffix = studioSessionQuery("#/tenders?notice=notice-2&chat=websocket%3Ateam%2Fbid%26draft");
    expect(new URLSearchParams(suffix).get("session_key")).toBe("websocket:team/bid&draft");
    expect(new URLSearchParams(suffix).has("notice")).toBe(false);
  });

  it("keeps a desk without a chat in its configured default workspace", () => {
    expect(studioSessionQuery("#/career?pane=offers")).toBe("");
    expect(studioSessionQuery("#/marketing")).toBe("");
    expect(studioSessionQuery("#/career?chat=telegram%3Aexample")).toBe("");
  });
});
