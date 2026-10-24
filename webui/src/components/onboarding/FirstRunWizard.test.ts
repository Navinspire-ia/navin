// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { FirstRunWizard } from "./FirstRunWizard";

const render = (open = true) =>
  renderToStaticMarkup(createElement(FirstRunWizard, { open, onComplete: () => undefined }));

describe("First-run wizard", () => {
  it("asks for the language and nothing else", () => {
    const html = render();
    for (const code of ["en", "fr", "ar"]) {
      expect(html).toContain(`data-testid="first-run-language-${code}"`);
    }
    // Arabic reads right to left.
    expect(html).toMatch(/lang="ar" dir="rtl"/);
    expect(html).toContain('data-testid="first-run-continue"');
    // No provider, plan or demo step any more: the provider bar handles models.
    expect(html).not.toContain("data-path=");
    expect(html).not.toContain("onboarding.wizard.stepOf");
  });

  it("keeps a scroll pane and stays draggable-safe in the desktop host", () => {
    const html = render();
    expect(html).toContain("host-no-drag");
    expect(html).toContain('data-testid="first-run-wizard-scroll"');
  });

  it("renders nothing once done", () => {
    expect(render(false)).toBe("");
  });
});
