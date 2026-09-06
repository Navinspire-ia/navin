import { readFileSync, existsSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const publicDir = path.resolve(__dirname, "../../public");
const srcDir = path.resolve(__dirname, "..");

describe("usage PWA Phase 1", () => {
  it("parses manifest.webmanifest with required install fields", () => {
    const manifestPath = path.join(publicDir, "manifest.webmanifest");
    expect(existsSync(manifestPath)).toBe(true);
    const manifest = JSON.parse(readFileSync(manifestPath, "utf8")) as {
      name?: string;
      short_name?: string;
      display?: string;
      start_url?: string;
      theme_color?: string;
      icons?: Array<{ src: string; sizes: string; type?: string }>;
    };
    expect(manifest.name).toBe("Navin");
    expect(manifest.short_name).toBe("Navin");
    expect(manifest.display).toBe("standalone");
    expect(manifest.start_url).toBe("/");
    expect(typeof manifest.theme_color).toBe("string");
    expect(manifest.icons?.length).toBeGreaterThanOrEqual(2);
    for (const icon of manifest.icons ?? []) {
      const iconPath = path.join(publicDir, icon.src.replace(/^\//, ""));
      expect(existsSync(iconPath), `missing icon ${icon.src}`).toBe(true);
    }
  });

  it("ships a minimal shell service worker", () => {
    const swPath = path.join(publicDir, "sw.js");
    expect(existsSync(swPath)).toBe(true);
    const sw = readFileSync(swPath, "utf8");
    expect(sw).toContain("CACHE_NAME");
    expect(sw).toContain("/manifest.webmanifest");
    expect(sw).toContain("caches.open");
    // Must not claim to cache API / AI traffic aggressively.
    expect(sw).toMatch(/shouldBypass|\/api\//);
  });

  it("registers the service worker from pwa.ts", () => {
    const pwaPath = path.join(srcDir, "pwa.ts");
    expect(existsSync(pwaPath)).toBe(true);
    const source = readFileSync(pwaPath, "utf8");
    expect(source).toContain("serviceWorker.register");
    expect(source).toContain("/sw.js");
    expect(source).toContain("registerUsagePwa");
  });
});
