import { describe, expect, it, vi } from "vitest";

import { pinElementToScrollStart } from "@/components/studio/tenders/wizard/pin-zone";

describe("pin zone section after open", () => {
  it("keeps the clicked region at the top of the scroll pane", () => {
    const header = { focus: vi.fn() };
    const section = {
      querySelector: vi.fn(() => header),
      scrollIntoView: vi.fn(),
    };
    pinElementToScrollStart(section as unknown as HTMLElement);
    expect(header.focus).toHaveBeenCalledWith({ preventScroll: true });
    expect(section.scrollIntoView).toHaveBeenCalledWith({
      block: "start",
      inline: "nearest",
      behavior: "auto",
    });
  });

  it("does nothing when the section is missing", () => {
    expect(() => pinElementToScrollStart(null)).not.toThrow();
  });
});
