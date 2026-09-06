import type { ReactNode } from "react";
import { DefaultButton, PrimaryButton } from "@fluentui/react";
import type { MarketingCreative, MarketingDesk } from "@/lib/marketing-api";

const BUTTON_STYLES = {
  root: { minHeight: 40, cursor: "pointer" as const },
};

export function MediaThumb({
  src,
  kind,
  label,
}: {
  src?: string;
  kind?: string;
  label?: string;
}) {
  if (!src) return null;
  const isVideo = kind === "video" || /\.(mp4|webm|mov)(\?|$)/i.test(src);
  const isAudio = kind === "audio" || /\.(mp3|wav|m4a|ogg)(\?|$)/i.test(src);
  return (
    <figure className="mt-2 overflow-hidden rounded-xl border border-border/60 bg-muted/20">
      {isVideo ? (
        <video src={src} controls className="max-h-56 w-full bg-black object-contain" />
      ) : isAudio ? (
        <audio src={src} controls className="w-full px-3 py-2" />
      ) : (
        <img src={src} alt={label || ""} className="max-h-56 w-full object-cover" />
      )}
      {label ? <figcaption className="px-2 py-1 text-[11px] text-muted-foreground">{label}</figcaption> : null}
    </figure>
  );
}

export function ColorDots({ colors }: { colors?: string[] }) {
  if (!colors?.length) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-2">
      {colors.map((color) => (
        <span key={color} className="inline-flex items-center gap-2 text-xs">
          <span className="h-6 w-6 rounded-full border border-border/70" style={{ background: color }} />
          {color}
        </span>
      ))}
    </div>
  );
}

export function BrandVisuals({ desk }: { desk: MarketingDesk }) {
  const logo = desk.brand.logo || desk.harvest?.logo_preview || desk.harvest?.logo;
  const colors = desk.brand.colors?.length ? desk.brand.colors : desk.harvest?.colors;
  const fonts = desk.brand.fonts?.length ? desk.brand.fonts : desk.harvest?.fonts;
  return (
    <div className="mt-4 grid gap-3">
      {logo ? <MediaThumb src={logo} label="Logo" /> : null}
      <ColorDots colors={colors} />
      {fonts?.length ? <p className="text-xs text-muted-foreground">Fonts: {fonts.join(" · ")}</p> : null}
    </div>
  );
}

export function CreativeCard({
  item,
  busy,
  onGenerate,
  onVision,
  generateLabel,
}: {
  item: MarketingCreative;
  busy?: boolean;
  onGenerate: () => void;
  onVision: (verdict: "PASS" | "WARN" | "BLOCK") => void;
  generateLabel: string;
}) {
  return (
    <li className="rounded-xl border border-border/60 px-3 py-3">
      <p className="text-sm font-medium">
        {item.kind} · {item.placement}
      </p>
      <p className="text-xs uppercase tracking-wide text-muted-foreground">
        {item.status}
        {item.source ? ` · ${item.source}` : ""}
      </p>
      <MediaThumb src={item.preview} kind={item.kind} label={item.placement} />
      <p className="mt-1 text-pretty text-xs text-muted-foreground">{item.prompt}</p>
      <div className="mt-2 flex flex-wrap gap-2">
        <DefaultButton text={generateLabel} disabled={Boolean(busy)} onClick={onGenerate} styles={BUTTON_STYLES} />
        {(["PASS", "WARN", "BLOCK"] as const).map((verdict) => (
          <DefaultButton
            key={verdict}
            text={verdict}
            disabled={Boolean(busy)}
            onClick={() => onVision(verdict)}
            styles={BUTTON_STYLES}
          />
        ))}
      </div>
    </li>
  );
}

export function PaneActions({ children }: { children: ReactNode }) {
  return <div className="mb-3 flex flex-wrap gap-2">{children}</div>;
}

export { PrimaryButton, DefaultButton, BUTTON_STYLES };
