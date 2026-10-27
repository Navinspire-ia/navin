// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { APP_FACTORY_TARGETS, appFactoryCommand, parseAppFactoryCommand } from "./appFactoryTargets";
import { PLATFORM_LOGO_IDS } from "./PlatformLogo";

describe("app factory targets", () => {
  it("lists the targets in order with their kind", () => {
    expect(APP_FACTORY_TARGETS.map((x) => x.id)).toEqual([
      "android", "ios", "windows", "macos", "ubuntu", "centos", "appimage",
    ]);
    expect(APP_FACTORY_TARGETS.filter((x) => x.kind === "mobile").map((x) => x.id)).toEqual(["android", "ios"]);
  });

  it("writes each target in os/<id>", () => {
    for (const target of APP_FACTORY_TARGETS) expect(target.folder).toBe(`os/${target.id}`);
  });

  it("builds the slash command", () => {
    expect(appFactoryCommand("windows")).toBe("/apps windows");
  });

  it("has a real platform mark for every target", () => {
    expect([...PLATFORM_LOGO_IDS].sort()).toEqual(APP_FACTORY_TARGETS.map((x) => x.id).sort());
  });

  it("reads back only the exact command the menus send", () => {
    expect(parseAppFactoryCommand("/apps android")?.label).toBe("Android");
    expect(parseAppFactoryCommand("  /apps  Windows ")?.id).toBe("windows");
    expect(parseAppFactoryCommand(appFactoryCommand("appimage"))?.folder).toBe("os/appimage");
    expect(parseAppFactoryCommand("/apps android and also add push notifications")).toBeNull();
    expect(parseAppFactoryCommand("/apps electron")).toBeNull();
    expect(parseAppFactoryCommand("/apps")).toBeNull();
    expect(parseAppFactoryCommand("android")).toBeNull();
  });
});
