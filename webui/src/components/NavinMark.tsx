import { cn } from "@/lib/utils";

/**
 * The brand mark drawn in the current text colour.
 *
 * The file under ``public/logo`` keeps its blue plate because it is also the
 * favicon, where a monochrome mark would vanish into the browser chrome. Inside
 * the app that plate was the only colour in an otherwise monochrome interface,
 * and it read as a sticker pasted over the sidebar rather than part of it, so
 * the glyph is inlined here and inherits the theme instead.
 */
export function NavinMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 64 64"
      fill="none"
      role="presentation"
      aria-hidden="true"
      className={cn("shrink-0", className)}
    >
      <path
        d="M18 44V20h7.2c1.4 0 2.5 1.1 2.5 2.5v14.2c0 0 4.4-9.1 9.6-10.5 5.4-1.4 8.3 2 8.3 2s-7.8-4-12.5 4.8C28.6 41.2 24.8 41.2 24.8 41.2V24.5c0-1.4-1.1-2.5-2.5-2.5H18z"
        fill="currentColor"
      />
      <path
        d="M46 20v24h-7.2c-1.4 0-2.5-1.1-2.5-2.5V27.3c0 0-4.4 9.1-9.6 10.5-5.4 1.4-8.3-2-8.3-2s7.8 4 12.5-4.8c4.5-8.5 8.3-8.5 8.3-8.5v16.7c0 1.4 1.1 2.5 2.5 2.5H46z"
        fill="currentColor"
        opacity="0.55"
      />
    </svg>
  );
}
