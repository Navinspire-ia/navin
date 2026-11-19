// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  addAnnotation,
  getAnnotationRequest,
  getAnnotations,
  parseReviewMessage,
  requestAnnotation,
  resetAnnotationsForTests,
} from "@/lib/annotations";

import { AnnotationDock } from "./AnnotationDock";
import { AnnotationLayer, AnnotationScopeContext } from "./AnnotationLayer";
import { ReviewFeedbackCard } from "./ReviewFeedbackCard";

afterEach(cleanup);
beforeEach(() => {
  resetAnnotationsForTests();
  window.getSelection()?.removeAllRanges();
});

function renderReview(onSend = vi.fn(), canSend = true) {
  render(
    <AnnotationScopeContext.Provider value="chat">
      <AnnotationLayer messageId="m1" messageKey="k1">
        <p data-testid="para">Use a message queue here.</p>
        <p>Then add a worker.</p>
      </AnnotationLayer>
      <AnnotationDock scope="chat" onSend={onSend} canSend={canSend} />
    </AnnotationScopeContext.Provider>,
  );
  return onSend;
}

/** Drag-selects characters [from, to) of the first paragraph. */
async function select(from: number, to: number) {
  const text = screen.getByTestId("para").firstChild as Text;
  const range = document.createRange();
  range.setStart(text, from);
  range.setEnd(text, to);
  fireEvent.mouseDown(text.parentElement as Element);
  const selection = window.getSelection() as Selection;
  selection.removeAllRanges();
  selection.addRange(range);
  await act(async () => {
    fireEvent.mouseUp(document);
    await new Promise((resolve) => setTimeout(resolve, 5));
  });
}

describe("annotating an answer", () => {
  it("opens the toolbar on a selection and tags it with one key", async () => {
    renderReview();
    await select(8, 15);
    expect(screen.getByTestId("annotation-toolbar")).toBeTruthy();
    expect(getAnnotationRequest()?.quote).toBe("message queue");

    fireEvent.keyDown(document, { key: "c" });
    expect(screen.queryByTestId("annotation-toolbar")).toBeNull();
    expect(getAnnotations("chat")).toMatchObject([
      { quote: "message queue", offset: 6, action: "clarify", source: { kind: "message", id: "m1" } },
    ]);
    expect(screen.getByTestId("annotation-tray").textContent).toContain("Review: 1 annotation");
  });

  it("combines an action with a typed comment", async () => {
    renderReview();
    await select(0, 3);
    fireEvent.change(screen.getByTestId("annotation-comment"), { target: { value: "too vague" } });
    fireEvent.click(screen.getByTestId("annotation-action-alternative"));
    expect(getAnnotations("chat")).toMatchObject([{ action: "alternative", comment: "too vague", quote: "Use" }]);
  });

  it("ignores a collapsed selection and closes on Escape", async () => {
    renderReview();
    await select(4, 4);
    expect(screen.queryByTestId("annotation-toolbar")).toBeNull();
    await select(0, 3);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByTestId("annotation-toolbar")).toBeNull();
    expect(getAnnotations("chat")).toHaveLength(0);
  });

  it("sends a single comment right away with Ctrl+Enter", async () => {
    const onSend = renderReview();
    await select(8, 15);
    const box = screen.getByTestId("annotation-comment");
    fireEvent.change(box, { target: { value: "which one?" } });
    fireEvent.keyDown(box, { key: "Enter", ctrlKey: true });
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(parseReviewMessage(onSend.mock.calls[0][0])).toMatchObject([
      { quote: "message queue", comment: "which one?" },
    ]);
    expect(getAnnotations("chat")).toHaveLength(0);
  });
});

describe("review tray", () => {
  const source = { kind: "message", id: "m1", key: "k1" } as const;

  it("sends every annotation as one message, then empties", () => {
    addAnnotation("chat", { source, quote: "worker", offset: 40, action: "keep", comment: "" });
    addAnnotation("chat", { source, quote: "message queue", offset: 6, action: "clarify", comment: "" });
    const onSend = renderReview();
    fireEvent.click(screen.getByTestId("annotation-send"));
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(parseReviewMessage(onSend.mock.calls[0][0])?.map((item) => item.quote)).toEqual([
      "message queue",
      "worker",
    ]);
    expect(getAnnotations("chat")).toHaveLength(0);
    expect(screen.queryByTestId("annotation-tray")).toBeNull();
  });

  it("holds the batch while the agent is answering", () => {
    addAnnotation("chat", { source, quote: "worker", offset: 40, action: "keep", comment: "" });
    const onSend = renderReview(vi.fn(), false);
    fireEvent.click(screen.getByTestId("annotation-send"));
    expect(onSend).not.toHaveBeenCalled();
    expect(getAnnotations("chat")).toHaveLength(1);
  });

  it("edits and removes a draft", () => {
    addAnnotation("chat", { source, quote: "worker", offset: 40, action: "keep", comment: "" });
    renderReview();
    fireEvent.click(screen.getByText("Review: 1 annotation"));
    fireEvent.change(screen.getByPlaceholderText("Add a comment (optional)"), { target: { value: "good" } });
    expect(getAnnotations("chat")[0].comment).toBe("good");
    fireEvent.click(screen.getByLabelText("Remove annotation"));
    expect(getAnnotations("chat")).toHaveLength(0);
  });
});

describe("annotating a file", () => {
  it("lands an editor selection in the conversation on screen", () => {
    const onSend = vi.fn();
    render(<AnnotationDock scope="chat" onSend={onSend} canSend />);
    act(() => {
      requestAnnotation({
        source: { kind: "file", path: "src/app.ts", fromLine: 4, toLine: 6 },
        quote: "const retries = 3;",
        offset: 0,
        rect: { left: 10, top: 10, right: 40, bottom: 20 },
      });
    });
    expect(getAnnotationRequest()?.scope).toBe("chat");
    fireEvent.keyDown(document, { key: "r" });
    fireEvent.click(screen.getByTestId("annotation-send"));
    const [item] = parseReviewMessage(onSend.mock.calls[0][0]) ?? [];
    expect(item).toMatchObject({ action: "remove", location: "@src/app.ts (L4-6)", quote: "const retries = 3;" });
  });

  it("annotates the rendered preview of a markdown file", async () => {
    render(
      <AnnotationScopeContext.Provider value="chat">
        <AnnotationLayer filePath="docs/plan.md">
          <p data-testid="para">Use a message queue here.</p>
        </AnnotationLayer>
        <AnnotationDock scope="chat" onSend={vi.fn()} canSend />
      </AnnotationScopeContext.Provider>,
    );
    await select(8, 15);
    fireEvent.keyDown(document, { key: "e" });
    expect(getAnnotations("chat")).toMatchObject([
      { action: "example", quote: "message queue", source: { kind: "file", path: "docs/plan.md" } },
    ]);
  });

  it("moves the keyboard out of an editor so letters are not typed into the file", () => {
    render(
      <>
        <textarea data-testid="editor" />
        <AnnotationDock scope="chat" onSend={vi.fn()} canSend />
      </>,
    );
    screen.getByTestId("editor").focus();
    act(() => {
      requestAnnotation({
        source: { kind: "file", path: "a.ts", fromLine: 1, toLine: 1 },
        quote: "x",
        offset: 0,
        rect: { left: 0, top: 0, right: 0, bottom: 0 },
      });
    });
    expect(document.activeElement).toBe(screen.getByTestId("annotation-comment"));
  });
});

describe("sent feedback", () => {
  it("reads as a compact card that opens on its annotations", () => {
    render(
      <ReviewFeedbackCard
        items={[
          { action: "clarify", label: "Clarify", location: "", quote: "message queue", comment: "which one?" },
          { action: "keep", label: "Good, keep this", location: "@a.md (L2)", quote: "worker", comment: "" },
        ]}
      />,
    );
    expect(screen.getByTestId("review-feedback-card").textContent).toContain("Review: 2 annotations");
    expect(screen.queryByText("which one?")).toBeNull();
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText("which one?")).toBeTruthy();
    expect(screen.getByText("@a.md (L2)")).toBeTruthy();
  });
});
