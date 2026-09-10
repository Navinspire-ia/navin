// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { ideOauthCallbackUrl, OAUTH_CALLBACK_PATH } from "@/lib/social-oauth-callback";

const vite = { protocol: "http:", hostname: "localhost", port: "5173", host: "localhost:5173" };
const ide = { protocol: "http:", hostname: "127.0.0.1", port: "8766", host: "127.0.0.1:8766" };
const prod = { protocol: "https:", hostname: "desk.navin.live", port: "", host: "desk.navin.live" };

describe("ideOauthCallbackUrl", () => {
  it("never uses the Vite :5173 origin", () => {
    expect(ideOauthCallbackUrl("reddit", { location: vite })).toBe(
      `http://127.0.0.1:8766${OAUTH_CALLBACK_PATH}`,
    );
    expect(ideOauthCallbackUrl("instagram", { location: vite })).toBe("");
  });

  it("uses the local IDE gateway when the page is already that origin", () => {
    expect(ideOauthCallbackUrl("reddit", { location: ide })).toBe(
      `http://127.0.0.1:8766${OAUTH_CALLBACK_PATH}`,
    );
  });

  it("uses a public HTTPS install for every provider", () => {
    expect(
      ideOauthCallbackUrl("instagram", {
        location: vite,
        mediaBaseUrl: "https://desk.navin.live/",
      }),
    ).toBe(`https://desk.navin.live${OAUTH_CALLBACK_PATH}`);
    expect(ideOauthCallbackUrl("reddit", { location: prod })).toBe(
      `https://desk.navin.live${OAUTH_CALLBACK_PATH}`,
    );
  });

  it("prefers a saved or suggested callback", () => {
    expect(
      ideOauthCallbackUrl("reddit", {
        location: vite,
        saved: `http://127.0.0.1:8766${OAUTH_CALLBACK_PATH}`,
      }),
    ).toBe(`http://127.0.0.1:8766${OAUTH_CALLBACK_PATH}`);
    expect(
      ideOauthCallbackUrl("instagram", {
        location: vite,
        saved: `https://navin.example.com${OAUTH_CALLBACK_PATH}`,
      }),
    ).toBe("");
    expect(
      ideOauthCallbackUrl("linkedin", {
        location: vite,
        suggested: `https://navin.live${OAUTH_CALLBACK_PATH}`,
      }),
    ).toBe(`https://navin.live${OAUTH_CALLBACK_PATH}`);
  });
});
