import { describe, expect, it } from "vitest";

import {
  ACCEPT_ATTR,
  MAX_ATTACHMENT_BYTES,
  MAX_AUDIO_BYTES,
  MAX_VIDEO_BYTES,
  acceptedAttachmentKind,
  isAudioAttachment,
  isVideoAttachment,
} from "./useAttachedImages";

function fileOf(name: string, type: string): File {
  return new File([new Uint8Array([1, 2, 3])], name, { type });
}

describe("acceptedAttachmentKind", () => {
  it("accepts the gateway image whitelist", () => {
    expect(acceptedAttachmentKind(fileOf("a.png", "image/png"))).toBe("image");
    expect(acceptedAttachmentKind(fileOf("a.jpg", "image/jpeg"))).toBe("image");
    expect(acceptedAttachmentKind(fileOf("a.webp", "image/webp"))).toBe("image");
    expect(acceptedAttachmentKind(fileOf("a.gif", "image/gif"))).toBe("image");
  });

  it("accepts the gateway video whitelist as raw files", () => {
    expect(acceptedAttachmentKind(fileOf("clip.mp4", "video/mp4"))).toBe("file");
    expect(acceptedAttachmentKind(fileOf("clip.webm", "video/webm"))).toBe("file");
    expect(acceptedAttachmentKind(fileOf("clip.mov", "video/quicktime"))).toBe("file");
  });

  it("accepts a video whose browser type is missing, by extension", () => {
    expect(acceptedAttachmentKind(fileOf("clip.mov", ""))).toBe("file");
    expect(acceptedAttachmentKind(fileOf("clip.mp4", "application/octet-stream"))).toBe("file");
  });

  it("still accepts documents", () => {
    expect(acceptedAttachmentKind(fileOf("a.pdf", "application/pdf"))).toBe("file");
    expect(acceptedAttachmentKind(fileOf("a.md", "text/markdown"))).toBe("file");
  });

  it("accepts the gateway audio whitelist as raw files", () => {
    expect(acceptedAttachmentKind(fileOf("memo.mp3", "audio/mpeg"))).toBe("file");
    expect(acceptedAttachmentKind(fileOf("memo.wav", "audio/wav"))).toBe("file");
    expect(acceptedAttachmentKind(fileOf("memo.m4a", "audio/x-m4a"))).toBe("file");
    expect(acceptedAttachmentKind(fileOf("memo.flac", "audio/flac"))).toBe("file");
  });

  it("accepts an audio file whose browser type is missing, by extension", () => {
    expect(acceptedAttachmentKind(fileOf("memo.mp3", ""))).toBe("file");
    expect(acceptedAttachmentKind(fileOf("memo.ogg", "application/octet-stream"))).toBe("file");
  });

  it("rejects unsupported types", () => {
    expect(acceptedAttachmentKind(fileOf("a.exe", "application/x-msdownload"))).toBeNull();
    expect(acceptedAttachmentKind(fileOf("a.avi", "video/x-msvideo"))).toBeNull();
    // Browser-recorded webm audio is the voice mode's format, not an upload type.
    expect(acceptedAttachmentKind(fileOf("a.weba", "audio/webm"))).toBeNull();
  });
});

describe("isAudioAttachment", () => {
  it("detects audio by type and by extension", () => {
    expect(isAudioAttachment(fileOf("memo.mp3", "audio/mpeg"))).toBe(true);
    expect(isAudioAttachment(fileOf("memo.wav", ""))).toBe(true);
    expect(isAudioAttachment(fileOf("clip.mp4", "video/mp4"))).toBe(false);
    expect(isAudioAttachment(fileOf("a.pdf", "application/pdf"))).toBe(false);
  });

  it("shares the video ceiling, matching the gateway", () => {
    expect(MAX_AUDIO_BYTES).toBe(20 * 1024 * 1024);
    expect(MAX_AUDIO_BYTES).toBeGreaterThan(MAX_ATTACHMENT_BYTES);
  });
});

describe("isVideoAttachment", () => {
  it("detects videos by type and by extension", () => {
    expect(isVideoAttachment(fileOf("clip.mp4", "video/mp4"))).toBe(true);
    expect(isVideoAttachment(fileOf("clip.mov", ""))).toBe(true);
    expect(isVideoAttachment(fileOf("a.pdf", "application/pdf"))).toBe(false);
    expect(isVideoAttachment(fileOf("a.png", "image/png"))).toBe(false);
  });

  it("gives videos a larger ceiling than documents, matching the gateway", () => {
    expect(MAX_VIDEO_BYTES).toBe(20 * 1024 * 1024);
    expect(MAX_VIDEO_BYTES).toBeGreaterThan(MAX_ATTACHMENT_BYTES);
  });
});

describe("ACCEPT_ATTR", () => {
  it("offers images, videos, audio and documents in the file picker", () => {
    expect(ACCEPT_ATTR).toContain("image/png");
    expect(ACCEPT_ATTR).toContain("video/mp4");
    expect(ACCEPT_ATTR).toContain("video/quicktime");
    expect(ACCEPT_ATTR).toContain("audio/mpeg");
    expect(ACCEPT_ATTR).toContain("application/pdf");
    expect(ACCEPT_ATTR).toContain(".mov");
    expect(ACCEPT_ATTR).toContain(".mp3");
  });
});
