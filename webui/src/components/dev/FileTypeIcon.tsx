import type { JSX } from "react";

import { cn } from "@/lib/utils";

import {
  resolveFileIconId,
  resolveFolderIconId,
  type FileIconId,
  type FolderIconId,
} from "./fileTypeResolve";

type SvgProps = { className?: string };

function LetterBadge({
  bg,
  fg = "#fff",
  letters,
  className,
}: SvgProps & { bg: string; fg?: string; letters: string }) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      <rect width="16" height="16" rx="3" fill={bg} />
      <text
        x="8"
        y="11.4"
        textAnchor="middle"
        fill={fg}
        fontSize="6.2"
        fontWeight="800"
        fontFamily="ui-sans-serif, system-ui, sans-serif"
      >
        {letters}
      </text>
    </svg>
  );
}

function ReactIcon({ className }: SvgProps) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      <circle cx="8" cy="8" r="1.25" fill="#61DAFB" />
      <g fill="none" stroke="#61DAFB" strokeWidth="1.1">
        <ellipse cx="8" cy="8" rx="6.1" ry="2.3" />
        <ellipse cx="8" cy="8" rx="6.1" ry="2.3" transform="rotate(60 8 8)" />
        <ellipse cx="8" cy="8" rx="6.1" ry="2.3" transform="rotate(120 8 8)" />
      </g>
    </svg>
  );
}

function PythonIcon({ className }: SvgProps) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      <path
        fill="#3776AB"
        d="M8.1 1.4c-3.2 0-3 .9-3 2.1v1.5h3.1v.3H3.6c-1.3 0-2.4 1-2.6 2.8-.2 2.1-.2 3.4 0 4.4.2 1.3 1.1 2.2 2.4 2.2h1.6V12c0-1.5 1.3-2.8 2.8-2.8h3.2c1.1 0 2-.9 2-2V6.3c0-1.2-.8-2.3-2.3-2.5-1.1-.2-2.2-.2-3.6-.2zm-1.7 1.7c.4 0 .7.3.7.7s-.3.7-.7.7-.7-.3-.7-.7.3-.7.7-.7z"
      />
      <path
        fill="#FFD43B"
        d="M10.9 5.2v2.8c0 1.6-1.3 2.9-2.8 2.9H4.9c-1.1 0-2 .9-2 2v1.8c0 1.2.9 1.9 2.4 2.2 1.8.4 3.5.4 4.8 0 1.1-.3 2.4-1 2.4-2.2V13H9.5v-.3h4.6c1.3 0 1.8-1 2-2.2.3-1.5.3-3 0-4.4-.2-1.2-1.1-2.2-2.4-2.2H10.9zm-1.5 8.1c.4 0 .7.3.7.7s-.3.7-.7.7-.7-.3-.7-.7.3-.7.7-.7z"
      />
    </svg>
  );
}

function DockerIcon({ className }: SvgProps) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      <g fill="#2496ED">
        <rect x="3.1" y="6.1" width="2" height="1.8" rx="0.25" />
        <rect x="5.4" y="6.1" width="2" height="1.8" rx="0.25" />
        <rect x="7.7" y="6.1" width="2" height="1.8" rx="0.25" />
        <rect x="5.4" y="4" width="2" height="1.8" rx="0.25" />
        <path d="M2.2 8.4h11.2c.2 2.1-1.2 3.8-3.6 4.2-2.4.3-5.4.1-7.1-.8-1-.6-1.4-1.8-1.5-3.4h1z" />
      </g>
    </svg>
  );
}

function GitIcon({ className }: SvgProps) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      <path
        fill="#F05032"
        d="M14.6 7.4 8.6 1.4a1 1 0 0 0-1.4 0L5.7 2.9l1.7 1.7a1.15 1.15 0 0 1 1.46 1.46l1.66 1.66a1.15 1.15 0 1 1-.66.66L8.3 6.85v4.36a1.15 1.15 0 1 1-.96-.02V6.76A1.15 1.15 0 0 1 6.7 5.2L5 3.5 1.4 7.1a1 1 0 0 0 0 1.4l6 6a1 1 0 0 0 1.4 0l6-6a1 1 0 0 0-.2-1.1z"
      />
    </svg>
  );
}

function EnvIcon({ className }: SvgProps) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      <circle cx="8" cy="8" r="6.2" fill="#5A6672" />
      <path
        fill="#F4F7FA"
        d="M8 5.1a1.1 1.1 0 0 1 1.1 1.1v.35l.95.55a.55.55 0 0 1 .2.75l-.85 1.47a.55.55 0 0 1-.7.22l-.95-.4v.9a.55.55 0 0 1-.55.55H6.8a.55.55 0 0 1-.55-.55v-.9l-.95.4a.55.55 0 0 1-.7-.22L3.75 7.85a.55.55 0 0 1 .2-.75l.95-.55V6.2A1.1 1.1 0 0 1 6 5.1h2zm0 4.15A1.25 1.25 0 1 0 8 6.75a1.25 1.25 0 0 0 0 2.5z"
      />
    </svg>
  );
}

function MarkdownIcon({ className }: SvgProps) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      <rect width="16" height="16" rx="3" fill="#42A5F5" />
      <path
        fill="#fff"
        d="M3.2 5.1h1.5l1.3 2.6 1.3-2.6h1.5v5.8H7.4V8.1L6 10.6h-.4L4.2 8.1v2.8H3.2V5.1zm7.3 0h1.4l2 3.1V5.1h1.3v5.8h-1.4l-2-3.1v3.1h-1.3V5.1z"
      />
    </svg>
  );
}

function YamlIcon({ className }: SvgProps) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      <rect width="16" height="16" rx="3" fill="#CB171E" />
      <text
        x="8"
        y="11.4"
        textAnchor="middle"
        fill="#fff"
        fontSize="5.4"
        fontWeight="800"
        fontFamily="ui-sans-serif, system-ui, sans-serif"
      >
        YML
      </text>
    </svg>
  );
}

function JsonIcon({ className }: SvgProps) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      <rect width="16" height="16" rx="3" fill="#F5A623" />
      <text
        x="8"
        y="11.6"
        textAnchor="middle"
        fill="#1A1A1A"
        fontSize="8"
        fontWeight="800"
        fontFamily="ui-sans-serif, system-ui, sans-serif"
      >
        {"{}"}
      </text>
    </svg>
  );
}

function NpmIcon({ className }: SvgProps) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      <rect width="16" height="16" rx="2" fill="#CB3837" />
      <path fill="#fff" d="M3 3.4h10v9.2H8.6V5.6H7.4v7H3V3.4z" />
    </svg>
  );
}

function NextIcon({ className }: SvgProps) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      <circle cx="8" cy="8" r="6.4" fill="#000" />
      <path
        fill="#fff"
        d="M6.1 5.1h1.15l3.55 5.35V5.1H12v5.8h-1.15L7.3 5.55V10.9H6.1V5.1z"
      />
    </svg>
  );
}

function TailwindIcon({ className }: SvgProps) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      <path
        fill="#38BDF8"
        d="M8 3.2c-2.1 0-3.4 1.05-4 3.15 1.05-1.4 2.27-1.93 3.66-1.58.79.2 1.36.78 1.98 1.42.62.64 1.33 1.41 2.76 1.41 2.1 0 3.4-1.05 4-3.15-1.05 1.4-2.27 1.93-3.66 1.58-.79-.2-1.36-.78-1.98-1.42C10.14 3.97 9.43 3.2 8 3.2zM4 8.8c-2.1 0-3.4 1.05-4 3.15 1.05-1.4 2.27-1.93 3.66-1.58.79.2 1.36.78 1.98 1.42.62.64 1.33 1.41 2.76 1.41 2.1 0 3.4-1.05 4-3.15-1.05 1.4-2.27 1.93-3.66 1.58-.79-.2-1.36-.78-1.98-1.42C6.14 9.57 5.43 8.8 4 8.8z"
      />
    </svg>
  );
}

function ViteIcon({ className }: SvgProps) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      <path fill="#FFD62E" d="M8 1.6 14.6 13.4 8 11.6 1.4 13.4 8 1.6z" />
      <path fill="#646CFF" d="M8 11.6 14.6 13.4 8 14.6 1.4 13.4 8 11.6z" />
    </svg>
  );
}

function RustIcon({ className }: SvgProps) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      <circle cx="8" cy="8" r="6.3" fill="#DEA584" />
      <circle cx="8" cy="8" r="4.2" fill="none" stroke="#1A1A1A" strokeWidth="1.1" />
      <circle cx="8" cy="8" r="1.3" fill="#1A1A1A" />
    </svg>
  );
}

function GoIcon({ className }: SvgProps) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      <rect width="16" height="16" rx="3" fill="#00ADD8" />
      <text
        x="8"
        y="11.2"
        textAnchor="middle"
        fill="#fff"
        fontSize="6.5"
        fontWeight="800"
        fontFamily="ui-sans-serif, system-ui, sans-serif"
      >
        Go
      </text>
    </svg>
  );
}

function EslintIcon({ className }: SvgProps) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      <path fill="#4B32C3" d="M8 1.2 14.6 5v6L8 14.8 1.4 11V5L8 1.2z" />
      <path fill="#fff" d="M8 3.4 12.6 6v4L8 12.6 3.4 10V6L8 3.4z" />
    </svg>
  );
}

function ShellIcon({ className }: SvgProps) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      <rect width="16" height="16" rx="3" fill="#3EAF47" />
      <path
        fill="#fff"
        d="M4.2 5.1 7 8l-2.8 2.9-.9-.9L5.2 8 3.3 6l.9-.9zm4.2 6.3h4.4v1.2H8.4v-1.2z"
      />
    </svg>
  );
}

function SqlIcon({ className }: SvgProps) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      <ellipse cx="8" cy="4.2" rx="5.2" ry="2" fill="#336791" />
      <path
        fill="#336791"
        d="M2.8 4.2v7.4c0 1.1 2.3 2 5.2 2s5.2-.9 5.2-2V4.2c0 1.1-2.3 2-5.2 2s-5.2-.9-5.2-2z"
      />
    </svg>
  );
}

function ImageIcon({ className }: SvgProps) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      <rect width="16" height="16" rx="3" fill="#26A69A" />
      <circle cx="5.4" cy="5.6" r="1.3" fill="#fff" />
      <path fill="#fff" d="M2.6 12.4 6.2 8l2.1 2.2 2-2.6 3.1 4.8H2.6z" />
    </svg>
  );
}

function SvgFileIcon({ className }: SvgProps) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      <rect width="16" height="16" rx="3" fill="#FFB13B" />
      <text
        x="8"
        y="11.3"
        textAnchor="middle"
        fill="#1A1A1A"
        fontSize="5.6"
        fontWeight="800"
        fontFamily="ui-sans-serif, system-ui, sans-serif"
      >
        SVG
      </text>
    </svg>
  );
}

function LockIcon({ className }: SvgProps) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      <rect width="16" height="16" rx="3" fill="#7A7A7A" />
      <path
        fill="#fff"
        d="M8 3.6a2.2 2.2 0 0 1 2.2 2.2v1.1h.7c.4 0 .7.3.7.7v4.1c0 .4-.3.7-.7.7H5.1a.7.7 0 0 1-.7-.7V7.6c0-.4.3-.7.7-.7h.7V5.8A2.2 2.2 0 0 1 8 3.6zm0 1.3c-.5 0-.9.4-.9.9v1.1h1.8V5.8c0-.5-.4-.9-.9-.9z"
      />
    </svg>
  );
}

function TempIcon({ className }: SvgProps) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      <circle cx="8" cy="8" r="6.2" fill="#607D8B" />
      <circle cx="8" cy="8" r="4.4" fill="none" stroke="#fff" strokeWidth="1.2" />
      <path stroke="#fff" strokeWidth="1.2" strokeLinecap="round" d="M8 5.2v3.1l2 1.2" />
    </svg>
  );
}

function ZipIcon({ className }: SvgProps) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      <rect width="16" height="16" rx="3" fill="#AF7A3A" />
      <path fill="#F5D76E" d="M7.1 2.4h1.8v1.4H7.1zm0 2.6h1.8v1.4H7.1zm0 2.6h1.8v1.4H7.1z" />
      <rect x="6.4" y="10.2" width="3.2" height="3" rx="0.4" fill="#F5D76E" />
    </svg>
  );
}

function FileFallbackIcon({ className }: SvgProps) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      <path
        fill="#90A4AE"
        d="M4.2 1.6h5.1L12.8 5v9.4c0 .6-.5 1-1 1H4.2c-.6 0-1-.4-1-1V2.6c0-.6.4-1 1-1z"
      />
      <path fill="#CFD8DC" d="M9.3 1.6v3.2c0 .4.3.7.7.7h3" />
    </svg>
  );
}

function FolderShape({
  className,
  fill,
  open,
}: SvgProps & { fill: string; open?: boolean }) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      {open ? (
        <path
          fill={fill}
          d="M1.4 4.6h4.2l1.1 1.2h7.9c.6 0 1 .4 1 1v1.1H1.6L.9 13.2h13.8l.8-6.3c0-.6-.4-1-1-1H1.4V4.6z"
        />
      ) : (
        <path
          fill={fill}
          d="M1.6 3.8h4.1l1.2 1.3h7.5c.6 0 1 .4 1 1v6.5c0 .6-.4 1-1 1H1.6c-.6 0-1-.4-1-1V4.8c0-.6.4-1 1-1z"
        />
      )}
    </svg>
  );
}

const FILE_ICONS: Record<FileIconId, (props: SvgProps) => JSX.Element> = {
  android: (p) => <LetterBadge bg="#3DDC84" fg="#1A1A1A" letters="And" {...p} />,
  angular: (p) => <LetterBadge bg="#DD0031" letters="Ng" {...p} />,
  ansible: (p) => <LetterBadge bg="#EE0000" letters="An" {...p} />,
  apple: (p) => <LetterBadge bg="#555555" letters="iOS" {...p} />,
  astro: (p) => <LetterBadge bg="#FF5D01" letters="As" {...p} />,
  audio: (p) => <LetterBadge bg="#7E57C2" letters="Au" {...p} />,
  babel: (p) => <LetterBadge bg="#F9DC3E" fg="#1A1A1A" letters="Bb" {...p} />,
  binary: (p) => <LetterBadge bg="#546E7A" letters="Bin" {...p} />,
  bun: (p) => <LetterBadge bg="#FBF0DF" fg="#1A1A1A" letters="Bun" {...p} />,
  c: (p) => <LetterBadge bg="#00599C" letters="C" {...p} />,
  cmake: (p) => <LetterBadge bg="#064F8C" letters="CM" {...p} />,
  coffee: (p) => <LetterBadge bg="#2F2625" letters="Cof" {...p} />,
  composer: (p) => <LetterBadge bg="#885630" letters="Cmp" {...p} />,
  cpp: (p) => <LetterBadge bg="#00599C" letters="C++" {...p} />,
  csharp: (p) => <LetterBadge bg="#68217A" letters="C#" {...p} />,
  css: (p) => <LetterBadge bg="#1572B6" letters="CSS" {...p} />,
  csv: (p) => <LetterBadge bg="#217346" letters="CSV" {...p} />,
  cypress: (p) => <LetterBadge bg="#17202C" letters="Cy" {...p} />,
  dart: (p) => <LetterBadge bg="#0175C2" letters="Dt" {...p} />,
  deno: (p) => <LetterBadge bg="#000000" letters="De" {...p} />,
  diff: (p) => <LetterBadge bg="#E5534B" letters="+-" {...p} />,
  docker: DockerIcon,
  editorconfig: (p) => <LetterBadge bg="#6C6C6C" letters="EC" {...p} />,
  ejs: (p) => <LetterBadge bg="#A91E50" letters="EJS" {...p} />,
  elixir: (p) => <LetterBadge bg="#4B275F" letters="Ex" {...p} />,
  elm: (p) => <LetterBadge bg="#1293D8" letters="Elm" {...p} />,
  env: EnvIcon,
  eslint: EslintIcon,
  excel: (p) => <LetterBadge bg="#217346" letters="Xls" {...p} />,
  file: FileFallbackIcon,
  firebase: (p) => <LetterBadge bg="#FFCA28" fg="#1A1A1A" letters="Fb" {...p} />,
  font: (p) => <LetterBadge bg="#EF6C00" letters="Aa" {...p} />,
  git: GitIcon,
  go: GoIcon,
  gradle: (p) => <LetterBadge bg="#02303A" letters="Gr" {...p} />,
  graphql: (p) => <LetterBadge bg="#E535AB" letters="GQL" {...p} />,
  handlebars: (p) => <LetterBadge bg="#F0772B" letters="Hb" {...p} />,
  haskell: (p) => <LetterBadge bg="#5D4F85" letters="Hs" {...p} />,
  helm: (p) => <LetterBadge bg="#0F1689" letters="He" {...p} />,
  html: (p) => <LetterBadge bg="#E44D26" letters="HTML" {...p} />,
  image: ImageIcon,
  java: (p) => <LetterBadge bg="#E76F00" letters="Jv" {...p} />,
  javascript: (p) => <LetterBadge bg="#F7DF1E" fg="#1A1A1A" letters="JS" {...p} />,
  "javascript-test": (p) => <LetterBadge bg="#F5A623" fg="#1A1A1A" letters="JS" {...p} />,
  jest: (p) => <LetterBadge bg="#C21325" letters="Jst" {...p} />,
  json: JsonIcon,
  julia: (p) => <LetterBadge bg="#9558B2" letters="Jl" {...p} />,
  jupyter: (p) => <LetterBadge bg="#F37626" letters="Nb" {...p} />,
  kotlin: (p) => <LetterBadge bg="#7F52FF" letters="Kt" {...p} />,
  kubernetes: (p) => <LetterBadge bg="#326CE5" letters="K8s" {...p} />,
  laravel: (p) => <LetterBadge bg="#FF2D20" letters="Lv" {...p} />,
  less: (p) => <LetterBadge bg="#1D365D" letters="Le" {...p} />,
  license: (p) => <LetterBadge bg="#B0BEC5" fg="#1A1A1A" letters="©" {...p} />,
  lock: LockIcon,
  log: (p) => <LetterBadge bg="#607D8B" letters="Log" {...p} />,
  lua: (p) => <LetterBadge bg="#000080" letters="Lua" {...p} />,
  makefile: (p) => <LetterBadge bg="#6D4C41" letters="MK" {...p} />,
  markdown: MarkdownIcon,
  maven: (p) => <LetterBadge bg="#C71A36" letters="Mv" {...p} />,
  next: NextIcon,
  nginx: (p) => <LetterBadge bg="#009639" letters="Nx" {...p} />,
  nim: (p) => <LetterBadge bg="#FFE953" fg="#1A1A1A" letters="Nim" {...p} />,
  npm: NpmIcon,
  nuget: (p) => <LetterBadge bg="#004880" letters="Nu" {...p} />,
  objc: (p) => <LetterBadge bg="#438EFF" letters="Obj" {...p} />,
  pdf: (p) => <LetterBadge bg="#E5252A" letters="PDF" {...p} />,
  perl: (p) => <LetterBadge bg="#39457E" letters="Pl" {...p} />,
  php: (p) => <LetterBadge bg="#777BB4" letters="PHP" {...p} />,
  playwright: (p) => <LetterBadge bg="#2EAD33" letters="Pw" {...p} />,
  powerpoint: (p) => <LetterBadge bg="#D24726" letters="Ppt" {...p} />,
  prettier: (p) => <LetterBadge bg="#F7B93E" fg="#1A1A1A" letters="Pr" {...p} />,
  prisma: (p) => <LetterBadge bg="#2D3748" letters="P" {...p} />,
  proto: (p) => <LetterBadge bg="#40A5A5" letters="Pb" {...p} />,
  pug: (p) => <LetterBadge bg="#A86454" letters="Pug" {...p} />,
  python: PythonIcon,
  r: (p) => <LetterBadge bg="#276DC3" letters="R" {...p} />,
  react: ReactIcon,
  ruby: (p) => <LetterBadge bg="#CC342D" letters="Rb" {...p} />,
  rust: RustIcon,
  scala: (p) => <LetterBadge bg="#DC322F" letters="Sc" {...p} />,
  scss: (p) => <LetterBadge bg="#C6538C" letters="SC" {...p} />,
  shell: ShellIcon,
  solidity: (p) => <LetterBadge bg="#363636" letters="Sol" {...p} />,
  sql: SqlIcon,
  storybook: (p) => <LetterBadge bg="#FF4785" letters="Sb" {...p} />,
  svelte: (p) => <LetterBadge bg="#FF3E00" letters="Sv" {...p} />,
  svg: SvgFileIcon,
  swift: (p) => <LetterBadge bg="#F05138" letters="Sw" {...p} />,
  tailwind: TailwindIcon,
  temp: TempIcon,
  terraform: (p) => <LetterBadge bg="#7B42BC" letters="Tf" {...p} />,
  text: (p) => <LetterBadge bg="#90A4AE" letters="Txt" {...p} />,
  toml: (p) => <LetterBadge bg="#9C4221" letters="Cfg" {...p} />,
  tsconfig: (p) => <LetterBadge bg="#3178C6" letters="TS*" {...p} />,
  twig: (p) => <LetterBadge bg="#8BC34A" fg="#1A1A1A" letters="Tw" {...p} />,
  typescript: (p) => <LetterBadge bg="#3178C6" letters="TS" {...p} />,
  "typescript-test": (p) => <LetterBadge bg="#F5A623" letters="TS" {...p} />,
  video: (p) => <LetterBadge bg="#E53935" letters="Vid" {...p} />,
  vite: ViteIcon,
  vue: (p) => <LetterBadge bg="#41B883" letters="Vue" {...p} />,
  wasm: (p) => <LetterBadge bg="#654FF0" letters="Wasm" {...p} />,
  webpack: (p) => <LetterBadge bg="#8DD6F9" fg="#1A1A1A" letters="Wp" {...p} />,
  word: (p) => <LetterBadge bg="#2B579A" letters="Doc" {...p} />,
  xml: (p) => <LetterBadge bg="#E34F26" letters="XML" {...p} />,
  yaml: YamlIcon,
  zig: (p) => <LetterBadge bg="#F7A41D" fg="#1A1A1A" letters="Zig" {...p} />,
  zip: ZipIcon,
};

const FOLDER_FILL: Record<FolderIconId, string> = {
  folder: "#DCB67A",
  "folder-android": "#3DDC84",
  "folder-apple": "#A2AAAD",
  "folder-config": "#90A4AE",
  "folder-desktop": "#5C6BC0",
  "folder-dist": "#78909C",
  "folder-docker": "#2496ED",
  "folder-docs": "#42A5F5",
  "folder-git": "#F05032",
  "folder-github": "#24292F",
  "folder-i18n": "#26A69A",
  "folder-linux": "#FFB300",
  "folder-macos": "#90A4AE",
  "folder-node": "#8BC34A",
  "folder-public": "#26A69A",
  "folder-scripts": "#7E57C2",
  "folder-src": "#42A5F5",
  "folder-supabase": "#3ECF8E",
  "folder-test": "#66BB6A",
  "folder-vendor": "#8D6E63",
  "folder-vscode": "#007ACC",
  "folder-windows": "#00A4EF",
};

export function FileTypeIcon({
  name,
  className,
}: {
  name: string;
  className?: string;
}) {
  const id = resolveFileIconId(name);
  const Icon = FILE_ICONS[id];
  return <Icon className={cn("h-3.5 w-3.5 shrink-0", className)} />;
}

export function FolderTypeIcon({
  name,
  open = false,
  className,
}: {
  name: string;
  open?: boolean;
  className?: string;
}) {
  const id = resolveFolderIconId(name);
  return (
    <FolderShape
      className={cn("h-3.5 w-3.5 shrink-0", className)}
      fill={FOLDER_FILL[id]}
      open={open}
    />
  );
}
