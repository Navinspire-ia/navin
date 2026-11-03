// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { MessageBubble } from "@/components/MessageBubble";
import { portraitSpec } from "@/components/team/TeamPortrait";
import { setTeamPhotos } from "@/lib/team-photos";
import type { UIMessage } from "@/lib/types";

type MemoComponent = { $$typeof: symbol; type: unknown };

afterEach(() => {
  cleanup();
  setTeamPhotos({});
});

describe("MessageBubble memo", () => {
  it("is a React.memo wrapper so scroll ticks skip unchanged bubbles", () => {
    const wrapped = MessageBubble as unknown as MemoComponent;
    expect(wrapped.$$typeof).toBe(Symbol.for("react.memo"));
    expect(typeof wrapped.type).toBe("function");
  });
});

describe("a teammate's message", () => {
  const reply = (author: UIMessage["author"]): UIMessage => ({
    id: "team-e1", role: "assistant", content: "Login fixed: the token was refreshed too late.", author, createdAt: 1,
  });

  it("shows the teammate's face, name and job above its words", () => {
    render(<MessageBubble message={reply({ id: "mark", name: "Mark", title: "Senior Developer", avatar: "person", color: "#2f7de1", gender: "male" })} />);
    const byline = screen.getByTestId("teammate-byline");
    expect(byline.textContent).toBe("MarkSenior Developer");
    expect(byline.querySelector("[data-testid='teammate-portrait']")).not.toBeNull();
    expect(screen.getByText("Login fixed: the token was refreshed too late.")).toBeTruthy();
  });

  it("shows its uploaded photo once the team's photos are known", () => {
    const picture = "data:image/webp;base64,UklGRhYAAABXRUJQVlA4IAoAAAAQAQCdASoBAAEAAQAcJaQAA3AA/v3AgAA=";
    render(<MessageBubble message={reply({ id: "salma", name: "Salma", title: "QA Engineer", photo: "custom:0123456789ab" })} />);
    expect(screen.queryByTestId("teammate-photo")).toBeNull();
    act(() => setTeamPhotos({ salma: picture, ghost: "javascript:alert(1)" }));
    expect(screen.getByTestId("teammate-photo").getAttribute("src")).toBe(picture);
  });

  it("has no byline when Navin wrote it", () => {
    render(<MessageBubble message={{ id: "a1", role: "assistant", content: "Done.", createdAt: 1 }} />);
    expect(screen.queryByTestId("teammate-byline")).toBeNull();
  });
});

describe("drawn faces", () => {
  it("give each default teammate its own face and anyone else a stable one", () => {
    const team = ["michel", "mark", "salma", "ethan", "grace", "karim", "olivia", "lea", "jack", "ava", "nora", "ryan", "daniel", "lucas"];
    const faces = new Set(team.map((id) => JSON.stringify(portraitSpec(id))));
    expect(faces.size).toBe(team.length);
    expect(portraitSpec("sofia", "female")).toEqual(portraitSpec("sofia", "female"));
    expect(["long", "bob", "bun", "ponytail", "waves", "afro"]).toContain(portraitSpec("sofia", "female").hairStyle);
    expect(portraitSpec("omar", "female").beard).toBe(false);
  });
});
