// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/** Map a file or folder name to a VS Code-style icon id. */

export type FileIconId =
  | "android"
  | "angular"
  | "ansible"
  | "apple"
  | "astro"
  | "audio"
  | "babel"
  | "binary"
  | "bun"
  | "c"
  | "cmake"
  | "coffee"
  | "composer"
  | "cpp"
  | "csharp"
  | "css"
  | "csv"
  | "cypress"
  | "dart"
  | "deno"
  | "diff"
  | "docker"
  | "editorconfig"
  | "ejs"
  | "elixir"
  | "elm"
  | "env"
  | "eslint"
  | "excel"
  | "file"
  | "firebase"
  | "font"
  | "git"
  | "go"
  | "gradle"
  | "graphql"
  | "handlebars"
  | "haskell"
  | "helm"
  | "html"
  | "image"
  | "java"
  | "javascript"
  | "javascript-test"
  | "jest"
  | "json"
  | "julia"
  | "jupyter"
  | "kotlin"
  | "kubernetes"
  | "laravel"
  | "less"
  | "license"
  | "lock"
  | "log"
  | "lua"
  | "makefile"
  | "markdown"
  | "maven"
  | "next"
  | "nginx"
  | "nim"
  | "npm"
  | "nuget"
  | "objc"
  | "pdf"
  | "perl"
  | "php"
  | "playwright"
  | "powerpoint"
  | "prettier"
  | "prisma"
  | "proto"
  | "pug"
  | "python"
  | "r"
  | "react"
  | "ruby"
  | "rust"
  | "scala"
  | "scss"
  | "shell"
  | "solidity"
  | "sql"
  | "storybook"
  | "svelte"
  | "svg"
  | "swift"
  | "tailwind"
  | "temp"
  | "terraform"
  | "text"
  | "toml"
  | "tsconfig"
  | "twig"
  | "typescript"
  | "typescript-test"
  | "video"
  | "vite"
  | "vue"
  | "wasm"
  | "webpack"
  | "word"
  | "xml"
  | "yaml"
  | "zig"
  | "zip";

export type FolderIconId =
  | "folder"
  | "folder-android"
  | "folder-apple"
  | "folder-config"
  | "folder-desktop"
  | "folder-dist"
  | "folder-docker"
  | "folder-docs"
  | "folder-git"
  | "folder-github"
  | "folder-i18n"
  | "folder-linux"
  | "folder-macos"
  | "folder-node"
  | "folder-public"
  | "folder-scripts"
  | "folder-src"
  | "folder-supabase"
  | "folder-test"
  | "folder-vendor"
  | "folder-vscode"
  | "folder-windows";

function baseName(name: string): string {
  return name.replace(/[/\\]+$/, "").split(/[/\\]/).pop() || name;
}

const FOLDER_BY_NAME: Record<string, FolderIconId> = {
  src: "folder-src",
  lib: "folder-src",
  app: "folder-src",
  apps: "folder-src",
  source: "folder-src",
  components: "folder-src",
  hooks: "folder-src",
  types: "folder-src",
  typings: "folder-src",
  test: "folder-test",
  tests: "folder-test",
  __tests__: "folder-test",
  spec: "folder-test",
  specs: "folder-test",
  e2e: "folder-test",
  cypress: "folder-test",
  playwright: "folder-test",
  fixtures: "folder-test",
  mocks: "folder-test",
  __mocks__: "folder-test",
  snapshots: "folder-test",
  coverage: "folder-test",
  node_modules: "folder-node",
  vendor: "folder-vendor",
  third_party: "folder-vendor",
  ".git": "folder-git",
  ".github": "folder-github",
  ".vscode": "folder-vscode",
  ".cursor": "folder-vscode",
  dist: "folder-dist",
  build: "folder-dist",
  out: "folder-dist",
  target: "folder-dist",
  release: "folder-dist",
  public: "folder-public",
  static: "folder-public",
  assets: "folder-public",
  images: "folder-public",
  fonts: "folder-public",
  media: "folder-public",
  docs: "folder-docs",
  doc: "folder-docs",
  documentation: "folder-docs",
  scripts: "folder-scripts",
  script: "folder-scripts",
  bin: "folder-scripts",
  config: "folder-config",
  configs: "folder-config",
  conf: "folder-config",
  ".config": "folder-config",
  supabase: "folder-supabase",
  desktop: "folder-desktop",
  linux: "folder-linux",
  windows: "folder-windows",
  macos: "folder-macos",
  darwin: "folder-macos",
  os: "folder-desktop",
  packaging: "folder-config",
  docker: "folder-docker",
  ".docker": "folder-docker",
  k8s: "folder-config",
  kubernetes: "folder-config",
  helm: "folder-config",
  android: "folder-android",
  ios: "folder-apple",
  macosx: "folder-apple",
  locales: "folder-i18n",
  locale: "folder-i18n",
  i18n: "folder-i18n",
  l10n: "folder-i18n",
  translations: "folder-i18n",
  messages: "folder-i18n",
};

const NAMED_FILE: Record<string, FileIconId> = {
  dockerfile: "docker",
  containerfile: "docker",
  ".dockerignore": "docker",
  "compose.yaml": "docker",
  "compose.yml": "docker",
  ".gitignore": "git",
  ".gitattributes": "git",
  ".gitmodules": "git",
  ".gitkeep": "git",
  ".mailmap": "git",
  "package.json": "npm",
  "package-lock.json": "lock",
  "yarn.lock": "lock",
  "pnpm-lock.yaml": "lock",
  "bun.lock": "lock",
  "bun.lockb": "lock",
  "npm-shrinkwrap.json": "lock",
  "cargo.lock": "rust",
  "cargo.toml": "rust",
  "rust-toolchain.toml": "rust",
  "rustfmt.toml": "rust",
  "clippy.toml": "rust",
  "go.mod": "go",
  "go.sum": "go",
  "pyproject.toml": "python",
  "requirements.txt": "python",
  pipfile: "python",
  "pipfile.lock": "python",
  "setup.py": "python",
  "setup.cfg": "python",
  "poetry.lock": "python",
  makefile: "makefile",
  gnumakefile: "makefile",
  "cmakelists.txt": "cmake",
  ".editorconfig": "editorconfig",
  license: "license",
  licence: "license",
  "nginx.conf": "nginx",
  "schema.prisma": "prisma",
  "composer.json": "composer",
  "composer.lock": "composer",
  gemfile: "ruby",
  "gemfile.lock": "ruby",
  rakefile: "ruby",
  "pom.xml": "maven",
  "build.gradle": "gradle",
  "build.gradle.kts": "gradle",
  "settings.gradle": "gradle",
  "settings.gradle.kts": "gradle",
  "androidmanifest.xml": "android",
  "info.plist": "apple",
  "pubspec.yaml": "dart",
  "pubspec.lock": "dart",
  "angular.json": "angular",
  "firebase.json": "firebase",
  ".firebaserc": "firebase",
  "webpack.config.js": "webpack",
  "webpack.config.ts": "webpack",
  "webpack.config.mjs": "webpack",
  "babel.config.js": "babel",
  "babel.config.json": "babel",
  ".babelrc": "babel",
  ".babelrc.js": "babel",
  "jest.config.js": "jest",
  "jest.config.ts": "jest",
  "jest.config.mjs": "jest",
  "playwright.config.ts": "playwright",
  "playwright.config.js": "playwright",
  "cypress.config.ts": "cypress",
  "cypress.config.js": "cypress",
  "bunfig.toml": "bun",
  "deno.json": "deno",
  "deno.jsonc": "deno",
  "vite.config.ts": "vite",
  "vite.config.js": "vite",
  "vite.config.mjs": "vite",
  "vitest.config.ts": "vite",
  "vitest.config.js": "vite",
  "vite-env.d.ts": "vite",
  "next.config.ts": "next",
  "next.config.js": "next",
  "next.config.mjs": "next",
  "next-env.d.ts": "next",
  "tailwind.config.ts": "tailwind",
  "tailwind.config.js": "tailwind",
  "tailwind.config.cjs": "tailwind",
  "tailwind.css": "tailwind",
  "nuxt.config.ts": "vue",
  "nuxt.config.js": "vue",
  "svelte.config.js": "svelte",
  "astro.config.mjs": "astro",
  "astro.config.ts": "astro",
  "chart.yaml": "helm",
  "chart.yml": "helm",
  "ansible.cfg": "ansible",
};

const EXT_TO_ICON: Record<string, FileIconId> = {
  tsx: "react",
  jsx: "react",
  ts: "typescript",
  mts: "typescript",
  cts: "typescript",
  js: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  py: "python",
  pyi: "python",
  pyw: "python",
  pyc: "python",
  rs: "rust",
  go: "go",
  php: "php",
  phtml: "php",
  php3: "php",
  php4: "php",
  php5: "php",
  phar: "php",
  java: "java",
  jar: "java",
  class: "java",
  kt: "kotlin",
  kts: "kotlin",
  swift: "swift",
  cs: "csharp",
  csx: "csharp",
  fs: "csharp",
  fsx: "csharp",
  vb: "csharp",
  c: "c",
  h: "c",
  cc: "cpp",
  cpp: "cpp",
  cxx: "cpp",
  hpp: "cpp",
  hxx: "cpp",
  hh: "cpp",
  m: "objc",
  mm: "objc",
  rb: "ruby",
  erb: "ruby",
  rake: "ruby",
  gemspec: "ruby",
  vue: "vue",
  svelte: "svelte",
  astro: "astro",
  html: "html",
  htm: "html",
  xhtml: "html",
  css: "css",
  scss: "scss",
  sass: "scss",
  less: "less",
  styl: "less",
  stylus: "less",
  json: "json",
  jsonc: "json",
  json5: "json",
  md: "markdown",
  mdx: "markdown",
  markdown: "markdown",
  rst: "markdown",
  adoc: "markdown",
  yml: "yaml",
  yaml: "yaml",
  toml: "toml",
  ini: "toml",
  cfg: "toml",
  conf: "toml",
  properties: "toml",
  xml: "xml",
  xsl: "xml",
  xslt: "xml",
  plist: "apple",
  svg: "svg",
  png: "image",
  jpg: "image",
  jpeg: "image",
  gif: "image",
  webp: "image",
  ico: "image",
  bmp: "image",
  avif: "image",
  heic: "image",
  tif: "image",
  tiff: "image",
  psd: "image",
  sh: "shell",
  bash: "shell",
  zsh: "shell",
  fish: "shell",
  ksh: "shell",
  ps1: "shell",
  psm1: "shell",
  bat: "shell",
  cmd: "shell",
  sql: "sql",
  db: "sql",
  sqlite: "sql",
  sqlite3: "sql",
  graphql: "graphql",
  gql: "graphql",
  zip: "zip",
  tar: "zip",
  gz: "zip",
  tgz: "zip",
  rar: "zip",
  "7z": "zip",
  bz2: "zip",
  xz: "zip",
  pdf: "pdf",
  dart: "dart",
  lua: "lua",
  r: "r",
  rmd: "r",
  scala: "scala",
  sc: "scala",
  ex: "elixir",
  exs: "elixir",
  erl: "elixir",
  hrl: "elixir",
  hs: "haskell",
  lhs: "haskell",
  zig: "zig",
  nim: "nim",
  nims: "nim",
  sol: "solidity",
  proto: "proto",
  protobuf: "proto",
  csv: "csv",
  tsv: "csv",
  txt: "text",
  text: "text",
  log: "log",
  out: "log",
  woff: "font",
  woff2: "font",
  ttf: "font",
  otf: "font",
  eot: "font",
  mp3: "audio",
  wav: "audio",
  flac: "audio",
  ogg: "audio",
  aac: "audio",
  m4a: "audio",
  mp4: "video",
  webm: "video",
  mov: "video",
  mkv: "video",
  avi: "video",
  doc: "word",
  docx: "word",
  odt: "word",
  rtf: "word",
  xls: "excel",
  xlsx: "excel",
  ods: "excel",
  ppt: "powerpoint",
  pptx: "powerpoint",
  odp: "powerpoint",
  ipynb: "jupyter",
  wasm: "wasm",
  wat: "wasm",
  diff: "diff",
  patch: "diff",
  exe: "binary",
  dll: "binary",
  so: "binary",
  dylib: "binary",
  o: "binary",
  a: "binary",
  bin: "binary",
  apk: "android",
  aab: "android",
  gradle: "gradle",
  csproj: "nuget",
  fsproj: "nuget",
  sln: "nuget",
  vbproj: "nuget",
  nupkg: "nuget",
  pl: "perl",
  pm: "perl",
  t: "perl",
  jl: "julia",
  coffee: "coffee",
  litcoffee: "coffee",
  elm: "elm",
  res: "elm",
  resi: "elm",
  pug: "pug",
  jade: "pug",
  hbs: "handlebars",
  handlebars: "handlebars",
  ejs: "ejs",
  twig: "twig",
  blade: "laravel",
  tf: "terraform",
  tfvars: "terraform",
  hcl: "terraform",
  nginx: "nginx",
  prisma: "prisma",
  cmake: "cmake",
};

function isTestFile(lower: string): boolean {
  return /\.(?:test|spec|e2e)\.[^.]+$/i.test(lower);
}

function namedOrPrefix(lower: string): FileIconId | null {
  if (NAMED_FILE[lower]) return NAMED_FILE[lower];
  if (lower.startsWith("dockerfile.")) return "docker";
  if (lower.startsWith("docker-compose")) return "docker";
  if (lower.startsWith("tsconfig") && (lower.endsWith(".json") || lower.endsWith(".jsonc"))) {
    return "tsconfig";
  }
  if (lower.startsWith(".eslintrc") || lower.startsWith("eslint.config.")) return "eslint";
  if (lower.startsWith(".prettierrc") || lower.startsWith("prettier.config.")) return "prettier";
  if (lower.startsWith("next.config")) return "next";
  if (lower.startsWith("tailwind.config")) return "tailwind";
  if (lower.startsWith("vite.config") || lower.startsWith("vitest.config")) return "vite";
  if (lower.startsWith("webpack.config")) return "webpack";
  if (lower.startsWith("babel.config") || lower.startsWith(".babelrc")) return "babel";
  if (lower.startsWith("jest.config")) return "jest";
  if (lower.startsWith("playwright.config")) return "playwright";
  if (lower.startsWith("cypress.config")) return "cypress";
  if (lower.startsWith("astro.config")) return "astro";
  if (lower.startsWith("nuxt.config")) return "vue";
  if (lower.startsWith("svelte.config")) return "svelte";
  if (lower.startsWith("license.") || lower.startsWith("licence.")) return "license";
  if (lower.endsWith(".stories.ts") || lower.endsWith(".stories.tsx") || lower.endsWith(".stories.js")) {
    return "storybook";
  }
  if (lower.endsWith(".blade.php")) return "laravel";
  if (lower.endsWith(".nginx")) return "nginx";
  if (lower.endsWith(".tf") || lower.endsWith(".tfvars")) return "terraform";
  if (lower.endsWith(".tmp") || lower.endsWith(".temp") || lower.endsWith(".swp")) return "temp";
  if (lower === ".env" || lower.startsWith(".env.") || lower.endsWith(".env") || lower.includes(".env.")) {
    return "env";
  }
  if (lower.endsWith(".lock")) return "lock";
  return null;
}

export function resolveFolderIconId(name: string): FolderIconId {
  const lower = baseName(name).toLowerCase();
  return FOLDER_BY_NAME[lower] ?? "folder";
}

export function resolveFileIconId(name: string): FileIconId {
  const lower = baseName(name).toLowerCase();
  const named = namedOrPrefix(lower);
  if (named) return named;

  const ext = lower.includes(".") ? lower.slice(lower.lastIndexOf(".") + 1) : "";
  const icon = EXT_TO_ICON[ext];
  if (!icon) return "file";
  if (isTestFile(lower)) {
    if (icon === "typescript") return "typescript-test";
    if (icon === "javascript") return "javascript-test";
  }
  return icon;
}
