import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { CHAT_DENSITY_ATTRIBUTE, applyChatDensity } from "@/hooks/useLocalPreferences";

describe("chat density attribute", () => {
  it("stamps the preference on the given root", () => {
    const attributes = new Map<string, string>();
    const root = {
      setAttribute: (name: string, value: string) => attributes.set(name, value),
    } as unknown as HTMLElement;

    applyChatDensity("compact", root);
    expect(attributes.get(CHAT_DENSITY_ATTRIBUTE)).toBe("compact");
    applyChatDensity("comfortable", root);
    expect(attributes.get(CHAT_DENSITY_ATTRIBUTE)).toBe("comfortable");
  });

  it("is applied once at the app root and read by the code block wrapping", () => {
    const app = readFileSync(resolve(__dirname, "../App.tsx"), "utf8");
    const codeBlock = readFileSync(resolve(__dirname, "../components/CodeBlock.tsx"), "utf8");
    expect(app).toContain("useChatDensityAttribute();");
    // Settings > "Code wrapping" is a real control: CodeBlock follows it.
    expect(codeBlock).toContain("const { codeWrap } = useLocalPreferences();");
    expect(codeBlock).toContain("wrapLongLinesProp ?? codeWrap");
  });
});
