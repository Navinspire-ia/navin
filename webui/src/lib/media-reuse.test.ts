// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it, vi } from "vitest";

import {
  canReuseMedia,
  fetchReusableMediaFile,
  onMediaReuse,
  requestMediaReuse,
  reusableImageMime,
  reusedMediaFileName,
} from "@/lib/media-reuse";

function response(body: BlobPart, type: string, init?: { ok?: boolean; status?: number }): Response {
  return {
    ok: init?.ok ?? true,
    status: init?.status ?? 200,
    blob: async () => new Blob([body], { type }),
  } as Response;
}

describe("canReuseMedia", () => {
  it("accepts a signed image URL", () => {
    expect(canReuseMedia({ kind: "image", url: "/api/media/sig/payload" })).toBe(true);
  });

  it("rejects video and file kinds, which the composer cannot carry", () => {
    expect(canReuseMedia({ kind: "video", url: "/api/media/sig/payload" })).toBe(false);
    expect(canReuseMedia({ kind: "file", url: "/api/media/sig/payload" })).toBe(false);
  });

  it("rejects an attachment with no URL", () => {
    expect(canReuseMedia({ kind: "image" })).toBe(false);
  });

  it("rejects a blob URL, whose page may already have revoked it", () => {
    expect(canReuseMedia({ kind: "image", url: "blob:http://host/abc" })).toBe(false);
  });
});

describe("the reuse bus", () => {
  it("reports when nobody is listening so the caller can speak up", () => {
    expect(requestMediaReuse({ url: "/api/media/a/b" })).toBe(false);
  });

  it("delivers to subscribers until they unsubscribe", () => {
    const seen: string[] = [];
    const off = onMediaReuse((request) => seen.push(request.url));
    expect(requestMediaReuse({ url: "/api/media/a/b" })).toBe(true);
    off();
    expect(requestMediaReuse({ url: "/api/media/c/d" })).toBe(false);
    expect(seen).toEqual(["/api/media/a/b"]);
  });

  it("keeps delivering after one subscriber throws", () => {
    const seen: string[] = [];
    const offBroken = onMediaReuse(() => {
      throw new Error("boom");
    });
    const offGood = onMediaReuse((request) => seen.push(request.url));
    expect(requestMediaReuse({ url: "/api/media/a/b" })).toBe(true);
    offBroken();
    offGood();
    expect(seen).toEqual(["/api/media/a/b"]);
  });
});

describe("reusableImageMime", () => {
  it("trusts the served type when it is an accepted image", () => {
    expect(reusableImageMime("image/png")).toBe("image/png");
    expect(reusableImageMime("image/jpeg; charset=binary")).toBe("image/jpeg");
    expect(reusableImageMime("IMAGE/WEBP")).toBe("image/webp");
  });

  it("falls back to the name when the endpoint degraded the type", () => {
    expect(reusableImageMime("application/octet-stream", "img_ab12.png")).toBe("image/png");
    expect(reusableImageMime("", "/api/media/sig/payload.jpg")).toBe("image/jpeg");
  });

  it("refuses types the composer would reject anyway", () => {
    expect(reusableImageMime("image/svg+xml", "logo.svg")).toBeNull();
    expect(reusableImageMime("video/mp4", "clip.mp4")).toBeNull();
    expect(reusableImageMime("application/octet-stream")).toBeNull();
  });
});

describe("reusedMediaFileName", () => {
  it("keeps a name whose extension already matches", () => {
    expect(reusedMediaFileName("img_ab12cd34.png", "image/png")).toBe("img_ab12cd34.png");
  });

  it("appends the right extension when the name lacks or contradicts one", () => {
    expect(reusedMediaFileName("shot", "image/png")).toBe("shot.png");
    expect(reusedMediaFileName("shot.gif", "image/png")).toBe("shot.gif.png");
  });

  it("never carries a path", () => {
    expect(reusedMediaFileName("generated/img_ab12.png", "image/png")).toBe("img_ab12.png");
  });

  it("names an anonymous media", () => {
    expect(reusedMediaFileName(undefined, "image/webp")).toBe("reused-image.webp");
  });
});

describe("fetchReusableMediaFile", () => {
  it("wraps the fetched bytes as a File the composer can enqueue", async () => {
    const fetchImpl = vi.fn(async () => response("png-bytes", "image/png"));
    const file = await fetchReusableMediaFile(
      { url: "/api/media/sig/payload", name: "img_ab12.png" },
      fetchImpl as unknown as typeof fetch,
    );
    expect(fetchImpl).toHaveBeenCalledWith("/api/media/sig/payload");
    expect(file.name).toBe("img_ab12.png");
    expect(file.type).toBe("image/png");
    expect(file.size).toBeGreaterThan(0);
  });

  it("reports a failed request instead of attaching an empty file", async () => {
    const fetchImpl = vi.fn(async () => response("", "image/png", { ok: false, status: 401 }));
    await expect(
      fetchReusableMediaFile({ url: "/api/media/bad/sig" }, fetchImpl as unknown as typeof fetch),
    ).rejects.toThrow(/401/);
  });

  it("refuses a media whose type is not attachable", async () => {
    const fetchImpl = vi.fn(async () => response("mp4-bytes", "video/mp4"));
    await expect(
      fetchReusableMediaFile(
        { url: "/api/media/sig/payload", name: "clip.mp4" },
        fetchImpl as unknown as typeof fetch,
      ),
    ).rejects.toThrow(/video\/mp4/);
  });
});
