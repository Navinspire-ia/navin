// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { resolveFileIconId, resolveFolderIconId } from "./fileTypeResolve";

describe("resolveFileIconId", () => {
  it("maps languages to their brand icons", () => {
    const cases: Array<[string, string]> = [
      ["app.js", "javascript"],
      ["util.mjs", "javascript"],
      ["legacy.cjs", "javascript"],
      ["app.spec.js", "javascript-test"],
      ["main.rs", "rust"],
      ["Cargo.toml", "rust"],
      ["Cargo.lock", "rust"],
      ["server.go", "go"],
      ["go.mod", "go"],
      ["index.php", "php"],
      ["index.phtml", "php"],
      ["composer.json", "composer"],
      ["view.blade.php", "laravel"],
      ["app.tsx", "react"],
      ["use-hook.ts", "typescript"],
      ["messaging.test.ts", "typescript-test"],
      ["main.py", "python"],
      ["App.java", "java"],
      ["Main.kt", "kotlin"],
      ["App.swift", "swift"],
      ["Program.cs", "csharp"],
      ["main.c", "c"],
      ["main.cpp", "cpp"],
      ["View.m", "objc"],
      ["app.rb", "ruby"],
      ["Gemfile", "ruby"],
      ["App.vue", "vue"],
      ["Widget.svelte", "svelte"],
      ["page.astro", "astro"],
      ["main.dart", "dart"],
      ["script.lua", "lua"],
      ["plot.R", "r"],
      ["App.scala", "scala"],
      ["mix.exs", "elixir"],
      ["Main.hs", "haskell"],
      ["main.zig", "zig"],
      ["lib.nim", "nim"],
      ["Token.sol", "solidity"],
      ["script.pl", "perl"],
      ["analysis.jl", "julia"],
      ["app.coffee", "coffee"],
      ["Main.elm", "elm"],
    ];
    for (const [name, icon] of cases) {
      expect(resolveFileIconId(name), name).toBe(icon);
    }
  });

  it("maps tooling and config files", () => {
    const cases: Array<[string, string]> = [
      ["Dockerfile", "docker"],
      [".dockerignore", "docker"],
      [".gitignore", "git"],
      [".env.local", "env"],
      ["backend-common.enc.env", "env"],
      ["backend-common.enc.env.tmp", "temp"],
      ["package.json", "npm"],
      ["package-lock.json", "lock"],
      ["tsconfig.json", "tsconfig"],
      [".eslintrc.json", "eslint"],
      ["next.config.ts", "next"],
      ["tailwind.config.ts", "tailwind"],
      ["vite.config.ts", "vite"],
      ["webpack.config.js", "webpack"],
      ["jest.config.ts", "jest"],
      ["playwright.config.ts", "playwright"],
      ["Button.stories.tsx", "storybook"],
      ["firebase.json", "firebase"],
      ["angular.json", "angular"],
      ["pubspec.yaml", "dart"],
      ["pom.xml", "maven"],
      ["build.gradle", "gradle"],
      ["App.csproj", "nuget"],
      ["CMakeLists.txt", "cmake"],
      ["ansible.cfg", "ansible"],
    ];
    for (const [name, icon] of cases) {
      expect(resolveFileIconId(name), name).toBe(icon);
    }
  });

  it("maps documents, media and data files", () => {
    const cases: Array<[string, string]> = [
      ["README.md", "markdown"],
      ["task.yaml", "yaml"],
      ["notes.txt", "text"],
      ["app.log", "log"],
      ["users.csv", "csv"],
      ["schema.proto", "proto"],
      ["report.pdf", "pdf"],
      ["brief.docx", "word"],
      ["sheet.xlsx", "excel"],
      ["deck.pptx", "powerpoint"],
      ["logo.svg", "svg"],
      ["photo.png", "image"],
      ["theme.woff2", "font"],
      ["track.mp3", "audio"],
      ["clip.mp4", "video"],
      ["notebook.ipynb", "jupyter"],
      ["module.wasm", "wasm"],
      ["fix.diff", "diff"],
      ["lib.so", "binary"],
      ["src/lib/api.ts", "typescript"],
    ];
    for (const [name, icon] of cases) {
      expect(resolveFileIconId(name), name).toBe(icon);
    }
  });

  it("does not throw on empty or odd names", () => {
    for (const name of ["", ".", "..", "CON", "файл.ts", "a.b.c.d.js", "noext"]) {
      expect(() => resolveFileIconId(name)).not.toThrow();
    }
    expect(resolveFileIconId("notes.xyz")).toBe("file");
    expect(resolveFileIconId("noext")).toBe("file");
  });
});

describe("resolveFolderIconId", () => {
  it("maps well-known folders", () => {
    expect(resolveFolderIconId("src")).toBe("folder-src");
    expect(resolveFolderIconId("node_modules")).toBe("folder-node");
    expect(resolveFolderIconId("vendor")).toBe("folder-vendor");
    expect(resolveFolderIconId(".git")).toBe("folder-git");
    expect(resolveFolderIconId("tests")).toBe("folder-test");
    expect(resolveFolderIconId("windows")).toBe("folder-windows");
    expect(resolveFolderIconId("android")).toBe("folder-android");
    expect(resolveFolderIconId("ios")).toBe("folder-apple");
    expect(resolveFolderIconId("docker")).toBe("folder-docker");
    expect(resolveFolderIconId("i18n")).toBe("folder-i18n");
    expect(resolveFolderIconId("random")).toBe("folder");
  });
});
