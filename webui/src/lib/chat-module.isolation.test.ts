// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { moduleSessionKey } from "./chat-module";

const sessions = [
  { key: "code-1", updatedAt: "2026-10-24T08:00:00Z", preview: "/forge fix" },
  { key: "tenders-old", updatedAt: "2026-10-20T08:00:00Z", preview: "" },
  { key: "tenders-new", updatedAt: "2026-10-23T08:00:00Z", preview: "" },
  { key: "plain", updatedAt: "2026-10-24T09:00:00Z", preview: "salut" },
];
const moduleByKey = { "code-1": "dev", "tenders-old": "tenders", "tenders-new": "tenders" };

describe("each desk keeps its own chat", () => {
  it("does not show the Code chat in Tenders", () => {
    expect(moduleSessionKey({ module: "tenders", activeKey: "code-1", carried: true, moduleByKey, sessions })).toBe("tenders-new");
  });

  it("does not carry a plain chat into a desk", () => {
    expect(moduleSessionKey({ module: "tenders", activeKey: "plain", carried: true, moduleByKey, sessions })).toBe("tenders-new");
  });

  it("opens a new chat for a desk that never had one", () => {
    expect(moduleSessionKey({ module: "career", activeKey: "code-1", carried: true, moduleByKey, sessions })).toBeNull();
  });

  it("keeps the desk's own session and a chat just started there", () => {
    expect(moduleSessionKey({ module: "tenders", activeKey: "tenders-old", carried: false, moduleByKey, sessions })).toBe("tenders-old");
    expect(moduleSessionKey({ module: "tenders", activeKey: "brand-new", carried: false, moduleByKey, sessions })).toBe("brand-new");
  });

  it("leaves Code and plain chat as they were", () => {
    expect(moduleSessionKey({ module: "dev", activeKey: "plain", carried: true, moduleByKey, sessions })).toBe("plain");
    expect(moduleSessionKey({ module: "chat", activeKey: "code-1", carried: true, moduleByKey, sessions })).toBe("code-1");
  });
});
