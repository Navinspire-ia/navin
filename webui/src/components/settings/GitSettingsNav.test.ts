// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import i18n from "@/i18n";
import { configurationCommand, configurationHash } from "@/lib/configuration-command";
import { createTokenUrl } from "@/lib/git-auth";
import { TOUR_PLACES } from "@/lib/ui-tour";

import { SETTINGS_NAV_ITEMS } from "./SettingsView";

describe("Git & GitHub settings entry", () => {
  it("is a top-level nav item right before Security", () => {
    const keys = SETTINGS_NAV_ITEMS.map((item) => item.key);
    const git = SETTINGS_NAV_ITEMS.find((item) => item.key === "git");
    expect(git?.fallback).toBe("Git & GitHub");
    expect(keys.indexOf("git")).toBe(keys.indexOf("advanced") - 1);
  });

  it("is labelled in every wired language", () => {
    expect(i18n.getFixedT("en")("settings.nav.git")).toBe("Git & GitHub");
    expect(i18n.getFixedT("fr")("settings.nav.git")).toBe("Git & GitHub");
    expect(i18n.getFixedT("ar")("settings.nav.git")).toBe("Git و GitHub");
    expect(i18n.getFixedT("en")("dev.git.forgeTokenMissing", { host: "h" })).toContain(
      "Settings > Git & GitHub",
    );
  });

  it("is deep-linkable", () => {
    expect(configurationHash("git")).toBe("#/settings?section=git");
    expect(configurationCommand("/settings git")).toEqual({ section: "git" });
    expect(configurationCommand("/github")).toEqual({ section: "git" });
    expect(TOUR_PLACES).toContain("settings/git");
  });

  it("links Create a token to the right forge page", () => {
    const github = createTokenUrl("github.com", "auto") ?? "";
    expect(github.startsWith("https://github.com/settings/tokens/new?")).toBe(true);
    expect(new URL(github).searchParams.get("scopes")).toBe("repo,read:org,workflow");
    expect(createTokenUrl("gitlab.example.com", "gitlab")).toContain(
      "https://gitlab.example.com/-/user_settings/personal_access_tokens",
    );
    expect(createTokenUrl("codeberg.org", "auto")).toBe("https://codeberg.org/user/settings/applications");
    expect(createTokenUrl("git.internal", "auto")).toBeNull();
  });
});
