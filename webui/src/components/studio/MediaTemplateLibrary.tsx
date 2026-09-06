import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  Aperture,
  Box,
  Check,
  Clapperboard,
  Image as ImageIcon,
  Languages,
  Layers,
  MapPin,
  Megaphone,
  Mic,
  Palette,
  Pin,
  Rocket,
  Search,
  ShoppingBag,
  SlidersHorizontal,
  Sparkles,
  Square,
  SwatchBook,
  Upload,
  User,
  Video,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { fetchMediaTemplates, type MediaTemplateItem } from "@/lib/api";
import { useClient } from "@/providers/ClientProvider";
import { cn } from "@/lib/utils";

const FAMILIES = [
  "stock",
  "style",
  "character",
  "element",
  "location",
  "structure",
  "color",
  "effects",
  "camera",
] as const;

/** Montage is an edit desk: grade, camera, finish, b-roll and sets only.
 * Faces, products and layout grids belong to the Marketing creation desk. */
const MONTAGE_FAMILIES = [
  "stock",
  "style",
  "location",
  "color",
  "effects",
  "camera",
] as const;

const PINNED = new Set(["style", "character"]);
const MONTAGE_PINNED = new Set(["style", "camera"]);

/** Each studio has its own visual identity so users never feel it is the
 * same tool twice: Marketing = violet creation desk, Montage = amber cutroom.
 * Tailwind needs static class strings, hence the explicit map. */
const IDENTITY = {
  marketing: {
    icon: Megaphone,
    labelKey: "studio.mediaLibrary.identity.marketing",
    labelFallback: "Brand creation desk",
    refsKey: "studio.mediaLibrary.allRefs",
    refsFallback: "Visual DNA",
    accentText: "text-violet-600 dark:text-violet-400",
    accentSoft: "bg-violet-500/10 text-violet-700 dark:text-violet-300",
    accentSolid: "bg-violet-600 text-white hover:bg-violet-700",
    accentBorder: "border-violet-500",
    accentDot: "bg-violet-500",
    kindActive: "bg-background text-violet-700 shadow-sm dark:text-violet-300",
  },
  montage: {
    icon: Clapperboard,
    labelKey: "studio.mediaLibrary.identity.montage",
    labelFallback: "Edit & cut desk",
    refsKey: "studio.mediaLibrary.allRefsMontage",
    refsFallback: "Cut references",
    accentText: "text-amber-600 dark:text-amber-400",
    accentSoft: "bg-amber-500/10 text-amber-700 dark:text-amber-300",
    accentSolid: "bg-amber-600 text-white hover:bg-amber-700",
    accentBorder: "border-amber-500",
    accentDot: "bg-amber-500",
    kindActive: "bg-background text-amber-700 shadow-sm dark:text-amber-300",
  },
} as const;

const TABS = ["photos", "illustrations", "designs"] as const;

const TAGS = [
  "startups",
  "aiagents",
  "fashion",
  "beauty",
  "portraits",
  "expressions",
  "photoshoots",
  "food",
  "backgrounds",
  "environments",
  "nature",
  "atmosphere",
] as const;

export const MAX_MEDIA_REFERENCES = 6;

/**
 * World languages supported by the STT / TTS providers, offered by the Montage
 * "Translate & dub" pane. `prompt` is the English name injected into the seed;
 * `label` is the native name shown in the UI. Free typing is also allowed.
 */
const DUB_LANGUAGES = [
  { code: "en", prompt: "English", label: "English" },
  { code: "fr", prompt: "French", label: "Français" },
  { code: "es", prompt: "Spanish", label: "Español" },
  { code: "de", prompt: "German", label: "Deutsch" },
  { code: "it", prompt: "Italian", label: "Italiano" },
  { code: "pt", prompt: "Portuguese", label: "Português" },
  { code: "ar", prompt: "Arabic", label: "العربية" },
  { code: "zh", prompt: "Mandarin Chinese", label: "中文" },
  { code: "ja", prompt: "Japanese", label: "日本語" },
  { code: "ko", prompt: "Korean", label: "한국어" },
  { code: "ru", prompt: "Russian", label: "Русский" },
  { code: "hi", prompt: "Hindi", label: "हिन्दी" },
  { code: "tr", prompt: "Turkish", label: "Türkçe" },
  { code: "nl", prompt: "Dutch", label: "Nederlands" },
  { code: "pl", prompt: "Polish", label: "Polski" },
  { code: "sv", prompt: "Swedish", label: "Svenska" },
  { code: "no", prompt: "Norwegian", label: "Norsk" },
  { code: "da", prompt: "Danish", label: "Dansk" },
  { code: "fi", prompt: "Finnish", label: "Suomi" },
  { code: "uk", prompt: "Ukrainian", label: "Українська" },
  { code: "el", prompt: "Greek", label: "Ελληνικά" },
  { code: "cs", prompt: "Czech", label: "Čeština" },
  { code: "ro", prompt: "Romanian", label: "Română" },
  { code: "hu", prompt: "Hungarian", label: "Magyar" },
  { code: "id", prompt: "Indonesian", label: "Bahasa Indonesia" },
  { code: "vi", prompt: "Vietnamese", label: "Tiếng Việt" },
  { code: "th", prompt: "Thai", label: "ไทย" },
] as const;

export type DubCloneMode = "catalog" | "source" | "custom";

function dubVoiceStep(clone: DubCloneMode): string {
  if (clone === "source") {
    return (
      `3) montage(action=voice_sample, path=<video>) - about 12s of source ` +
      `speech. 4) montage(action=voicetrack, srt=translated.srt, ` +
      `reference=<sample>) - one clip per cue, pinned to its timecode. If ` +
      `the TTS model cannot clone, stop and tell the user to pick Fish ` +
      `Audio (or ElevenLabs) in Settings > Voice. Do NOT call ` +
      `generate_speech on the whole text. `
    );
  }
  if (clone === "custom") {
    return (
      `3) Use the attached voice sample, or the provider voice id the user ` +
      `named. montage(action=voicetrack, srt=translated.srt, ` +
      `reference=<sample>) or voice=<id>. If the TTS model cannot clone, ` +
      `stop and say so. Do NOT call generate_speech on the whole text. `
    );
  }
  return (
    `3) montage(action=voicetrack, srt=translated.srt) - one clip per cue, ` +
    `pinned to its timecode, using the catalogue voice from Settings. Do ` +
    `NOT call generate_speech on the whole text. `
  );
}

function dubSeedText(
  modes: { voice: boolean; subs: boolean },
  sourceLanguage: string,
  targetLanguage: string,
  clone: DubCloneMode = "catalog",
): string {
  const target = targetLanguage.trim();
  const source = sourceLanguage.trim();
  const sourceText = source
    ? `The source language is ${source} (pass it as the transcribe language hint). `
    : `Auto-detect the source language. `;
  const shared =
    `1) montage(action=transcribe, path=<video>) - timed source.srt + ` +
    `transcript. 2) Translate the cues into ${target}: keep numbering and ` +
    `timing EXACTLY, natural spoken register, max ~42 chars per subtitle ` +
    `line, save as translated.srt. `;
  const tail =
    `If no video is attached, ask once for the file or URL. Never fake a ` +
    `step, never invent timings.\n\n`;
  if (modes.voice && modes.subs) {
    return (
      `/montage Translate the attached video into ${target}: DUB the voice AND ` +
      `burn translated subtitles. ${sourceText}${shared}` +
      `${dubVoiceStep(clone)}` +
      `Then montage(action=dub, path=<video>, voice=<voice_track>, ` +
      `original_gain_db=-22, srt=translated.srt). Deliver the MP4 via the ` +
      `message tool media parameter, plus the .srt path. ${tail}`
    );
  }
  if (modes.subs) {
    return (
      `/montage Add TRANSLATED subtitles in ${target} to the attached video ` +
      `(keep the original voice). ${sourceText}${shared}` +
      `3) Burn them: montage(action=assemble, visuals=<video>, ` +
      `srt=translated.srt) matching the source format. 4) Deliver the MP4 ` +
      `plus the .srt path. ${tail}`
    );
  }
  return (
    `/montage Translate and DUB the attached video into ${target}. ` +
    `${sourceText}` +
    `1) montage(action=doctor); setup ffmpeg only if missing. ${shared}` +
    `${dubVoiceStep(clone)}` +
    `Then montage(action=dub, path=<video>, voice=<voice_track>, ` +
    `original_gain_db=-22). Deliver the dubbed MP4 via the message tool ` +
    `media parameter. ${tail}`
  );
}

/**
 * Language input with a styled dropdown (the native datalist popup cannot be
 * themed and looked broken in dark mode). Opens on focus, filters while
 * typing, free text stays allowed.
 */
function DubLanguageField({
  label,
  value,
  onChange,
  placeholder,
  testId,
}: {
  label: string;
  value: string;
  onChange: (next: string) => void;
  placeholder: string;
  testId: string;
}) {
  const [open, setOpen] = useState(false);
  const query = value.trim().toLowerCase();
  const matches = DUB_LANGUAGES.filter(
    (lang) =>
      !query
      || lang.prompt.toLowerCase().includes(query)
      || lang.label.toLowerCase().includes(query),
  );
  return (
    <label className="relative block">
      <span className="text-[12px] font-medium text-muted-foreground">{label}</span>
      <input
        type="text"
        value={value}
        onChange={(event) => {
          onChange(event.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        placeholder={placeholder}
        className="mt-1.5 w-full rounded-xl border border-border/60 bg-background px-3 py-2 text-[13px] outline-none transition-colors focus:border-amber-500/70"
        data-testid={testId}
      />
      {open && matches.length > 0 ? (
        <div className="absolute left-0 right-0 top-full z-30 mt-1 max-h-56 overflow-y-auto rounded-xl border border-border/60 bg-popover p-1 shadow-lg">
          {matches.map((lang) => (
            <button
              key={lang.code}
              type="button"
              // mousedown fires before the input's blur: the click lands.
              onMouseDown={(event) => {
                event.preventDefault();
                onChange(lang.prompt);
                setOpen(false);
              }}
              className="flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-left text-[12.5px] transition-colors hover:bg-muted"
            >
              <span className="font-medium">{lang.label}</span>
              <span className="text-[11px] text-muted-foreground">{lang.prompt}</span>
            </button>
          ))}
        </div>
      ) : null}
    </label>
  );
}

const spring = { type: "spring" as const, duration: 0.3, bounce: 0 };

const FAMILY_ICONS = {
  stock: ImageIcon,
  style: Palette,
  character: User,
  element: Box,
  location: MapPin,
  structure: Layers,
  color: SwatchBook,
  effects: Sparkles,
  camera: Aperture,
} as const;

const FORMAT_ASPECT: Record<string, string> = {
  "9:16": "aspect-[9/16]",
  "1:1": "aspect-square",
  "21:9": "aspect-[21/9]",
  "4:5": "aspect-[4/5]",
};

type KindFilter = "image" | "video";

export function mediaTemplateSeedText(
  studio: "marketing" | "montage",
  items: MediaTemplateItem | MediaTemplateItem[],
): string {
  const command = studio === "montage" ? "/montage" : "/campaign";
  const list = Array.isArray(items) ? items : [items];
  if (list.length === 0) return "";
  if (list.length === 1) {
    const item = list[0];
    return (
      `${command} Use the attached media template "${item.title}" ` +
      `(${item.kind}, ${item.format}, ${item.family}). ` +
      `The file is already materialized in the workspace (see the runtime ` +
      `context local path) - build from that file, never re-download it. ` +
      `Do not invent a substitute.\n\n`
    );
  }
  const refs = list
    .map((item) => `"${item.title}" (${item.family}, ${item.kind}, ${item.format})`)
    .join(", ");
  return (
    `${command} Combine the ${list.length} attached media references into ONE output: ` +
    `${refs}. The files are already materialized in the workspace (runtime ` +
    `context local paths) - never re-download them. Each reference plays the role of ` +
    `its family (style = look, character = face, color = palette, element = product, ` +
    `location = set, structure = layout, camera = lens, effects = finish, ` +
    `stock = subject). Do not invent substitutes.\n\n`
  );
}

interface WorkflowSpec {
  id: string;
  /** Fallback title / description; i18n keys derive from the id. */
  title: string;
  description: string;
  prompt: string;
  /** Reference picks: first catalog item matching each spec is attached. */
  refs: { family: (typeof FAMILIES)[number]; kind: KindFilter; tab?: (typeof TABS)[number] }[];
}

const MARKETING_WORKFLOWS: WorkflowSpec[] = [
  {
    id: "perfectAd",
    title: "Perfect product ad",
    description: "Product + face + look + palette, one flawless visual.",
    prompt:
      "/campaign Create ONE perfect marketing image from the attached references: "
      + "the element is the product hero, the character is the face, the style sets "
      + "the look, the color locks the palette. Use the local workspace files, "
      + "generate with generate_image passing them as reference_images, keep "
      + "aspect 4:5, then propose 2 variations.\n\n",
    refs: [
      { family: "element", kind: "image" },
      { family: "character", kind: "image" },
      { family: "style", kind: "image" },
      { family: "color", kind: "image" },
    ],
  },
  {
    id: "videoAd",
    title: "9:16 video ad",
    description: "Vertical clip with camera, set and finish locked.",
    prompt:
      "/campaign Produce a 9:16 video ad from the attached references: the camera "
      + "reference defines lens and movement, the location is the set, the effects "
      + "reference is the finish. Use the local workspace files, generate with "
      + "generate_video (reference_image = the strongest still), then package "
      + "with montage for TikTok / Reels / Shorts.\n\n",
    refs: [
      { family: "camera", kind: "video" },
      { family: "location", kind: "video" },
      { family: "effects", kind: "video" },
    ],
  },
  {
    id: "fullCampaign",
    title: "Full mini campaign",
    description: "Offer, visuals, video and copy in one pass.",
    prompt:
      "/campaign Build a full mini campaign from the attached references: offer + "
      + "ICP + CTA, a hero image (generate_image with the references), a 9:16 clip "
      + "(generate_video), and the ad copy for 3 channels. Combine the references "
      + "into one coherent brand look.\n\n",
    refs: [
      { family: "element", kind: "image" },
      { family: "style", kind: "image" },
      { family: "color", kind: "image" },
    ],
  },
  {
    id: "moodboard",
    title: "Brand moodboard",
    description: "Style, palette and finish as a brand board.",
    prompt:
      "/campaign Build a brand moodboard from the attached references: extract the "
      + "look, palette and finish, generate 6 on-brand visuals with generate_image "
      + "(references attached), and lay them out as one board image.\n\n",
    refs: [
      { family: "style", kind: "image" },
      { family: "color", kind: "image" },
      { family: "effects", kind: "image" },
    ],
  },
  {
    id: "posterDesign",
    title: "Hero poster",
    description: "Layout grid + look + palette, one designed poster.",
    prompt:
      "/campaign Design ONE hero poster from the attached references: the layout "
      + "reference is the grid, the style sets typography and look, the color locks "
      + "the palette, the product is the hero. Generate the final visual with "
      + "generate_image (references attached), deliver 4:5 master plus 9:16 and 1:1 "
      + "crops. Save under marketing/creatives/posters/.\n\n",
    refs: [
      { family: "structure", kind: "image" },
      { family: "style", kind: "image" },
      { family: "color", kind: "image" },
      { family: "element", kind: "image" },
    ],
  },
  {
    id: "orbit3d",
    title: "3D product page",
    description: "Your product in an interactive orbit view.",
    prompt:
      "/campaign Build an interactive 3D product page: validate a packshot with "
      + "generate_image from the attached references, then a web page with three + "
      + "@react-three/fiber + @react-three/drei showing the product in orbit, stage "
      + "and lighting matched to the palette reference. Working controls, no stub "
      + "buttons. Save under marketing/creatives/orbit/.\n\n",
    refs: [
      { family: "element", kind: "image" },
      { family: "style", kind: "image" },
      { family: "color", kind: "image" },
    ],
  },
];

const MONTAGE_WORKFLOWS: WorkflowSpec[] = [
  {
    id: "socialCut",
    title: "9:16 social cut",
    description: "Cut and package a vertical clip like the references.",
    prompt:
      "/montage Cut a 9:16 social clip: the camera reference defines movement and "
      + "cut rhythm, the effects reference the finish. Generate missing shots with "
      + "generate_video (reference_image from the attached files), then "
      + "montage(action=package) for TikTok / Reels / Shorts.\n\n",
    refs: [
      { family: "camera", kind: "video" },
      { family: "effects", kind: "video" },
    ],
  },
  {
    id: "cinemaGrade",
    title: "Cinema grade",
    description: "Grade footage to match a look and a palette.",
    prompt:
      "/montage Grade the project footage to match the attached references: the "
      + "style reference is the look, the color reference locks the palette. Apply "
      + "with the montage tools and export a before / after.\n\n",
    refs: [
      { family: "style", kind: "image" },
      { family: "color", kind: "image" },
    ],
  },
  {
    id: "assembleEdit",
    title: "Multi-shot edit",
    description: "Assemble stock shots into one edit with music.",
    prompt:
      "/montage Assemble a multi-shot edit from the attached stock references: "
      + "match their pacing, add a music bed with generate_music, then "
      + "montage(action=assemble) and render a 16:9 master.\n\n",
    refs: [
      { family: "stock", kind: "video" },
      { family: "location", kind: "video" },
      { family: "camera", kind: "video" },
    ],
  },
  {
    id: "heroFrame",
    title: "Hero frame to video",
    description: "One perfect still, then bring it to life.",
    prompt:
      "/montage Create one perfect hero frame from the attached references with "
      + "generate_image (reference_images), validate it, then animate it with "
      + "generate_video (reference_image = the hero frame), same aspect ratio.\n\n",
    refs: [
      { family: "style", kind: "image" },
      { family: "camera", kind: "image" },
      { family: "location", kind: "image" },
    ],
  },
  {
    id: "motionTitle",
    title: "Motion title design",
    description: "Animated title card, designed then rendered.",
    prompt:
      "/montage Design an animated title card as an HTML composition under "
      + "marketing/montage/compositions/: typography and grade from the attached "
      + "Looks reference, palette locked to the Palettes reference. Preview a frame "
      + "with montage(action=screenshot), then montage(action=render) with the "
      + "platform profile I confirm.\n\n",
    refs: [
      { family: "style", kind: "image" },
      { family: "color", kind: "image" },
    ],
  },
];

/** Collage thumbnails must never repeat the same base photo, even when two
 * families render treated variants of one shot. */
function dedupeByBasePhoto(refs: MediaTemplateItem[]): MediaTemplateItem[] {
  const seen = new Set<string>();
  const result: MediaTemplateItem[] = [];
  for (const ref of refs) {
    const base = ref.preview_url.split("?")[0];
    if (seen.has(base)) continue;
    seen.add(base);
    result.push(ref);
  }
  return result.length > 0 ? result : refs.slice(0, 1);
}

function resolveWorkflowRefs(
  workflow: WorkflowSpec,
  items: MediaTemplateItem[],
): MediaTemplateItem[] {
  const picked: MediaTemplateItem[] = [];
  const used = new Set<string>();
  for (const spec of workflow.refs) {
    const match = items.find(
      (item) =>
        !used.has(item.id)
        && item.family === spec.family
        && item.kind === spec.kind
        && (!spec.tab || item.kind !== "image" || item.tab === spec.tab),
    );
    if (match) {
      used.add(match.id);
      picked.push(match);
    }
  }
  return picked.slice(0, MAX_MEDIA_REFERENCES);
}

export function MediaTemplateLibrary({
  studio,
  onUse,
  onSeed,
  onWorkflow,
  onLocalFiles,
}: {
  studio: "marketing" | "montage";
  /** Fired by "Add N": the whole reference tray (1..6 items). */
  onUse: (items: MediaTemplateItem[]) => void;
  onSeed?: (text: string, options?: { replace?: boolean }) => void;
  /** Getting-started card: ready prompt + auto-picked references. */
  onWorkflow?: (text: string, items: MediaTemplateItem[]) => void;
  onLocalFiles?: (files: File[]) => void;
}) {
  const { t } = useTranslation();
  const { token } = useClient();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const families = studio === "montage" ? MONTAGE_FAMILIES : FAMILIES;
  const pinned = studio === "montage" ? MONTAGE_PINNED : PINNED;
  const [items, setItems] = useState<MediaTemplateItem[]>([]);
  const [family, setFamily] = useState<(typeof FAMILIES)[number]>("stock");
  // Montage is an edit desk: motion first.
  const [kind, setKind] = useState<KindFilter>(
    studio === "montage" ? "video" : "image",
  );
  const [tab, setTab] = useState<(typeof TABS)[number]>("photos");
  const [tag, setTag] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [formatFilter, setFormatFilter] = useState<string | null>(null);
  const [premiumOnly, setPremiumOnly] = useState(false);
  const [tray, setTray] = useState<MediaTemplateItem[]>([]);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const imageRef = useRef<HTMLInputElement>(null);
  const videoRef = useRef<HTMLInputElement>(null);
  // Montage-only "Translate & dub" pane (sidebar entry above the families).
  const [pane, setPane] = useState<"library" | "dub">("library");
  // Voice and subtitles combine freely; at least one is required to seed.
  const [dubVoice, setDubVoice] = useState(true);
  const [dubSubs, setDubSubs] = useState(false);
  // Free-typed languages; source empty = auto-detect.
  const [dubSource, setDubSource] = useState("");
  const [dubTarget, setDubTarget] = useState("");
  const [dubClone, setDubClone] = useState<DubCloneMode>("catalog");
  const dubVideoRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    void fetchMediaTemplates(token, studio)
      .then((payload) => {
        if (!cancelled) setItems(payload.items);
      })
      .catch(() => {
        if (!cancelled) setItems([]);
      });
    return () => {
      cancelled = true;
    };
  }, [studio, token]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return items.filter((item) => {
      if (item.kind !== kind) return false;
      if (item.family !== family) return false;
      if (kind === "image" && item.tab !== tab) return false;
      if (tag && !item.tags.includes(tag)) return false;
      if (formatFilter && item.format !== formatFilter) return false;
      if (premiumOnly && !item.premium) return false;
      if (
        q
        && !item.title.toLowerCase().includes(q)
        && !item.tags.some((entry) => entry.includes(q))
        && !item.format.includes(q)
      ) {
        return false;
      }
      return true;
    });
  }, [family, formatFilter, items, kind, premiumOnly, query, tab, tag]);

  const filtersActive = formatFilter !== null || premiumOnly;

  // Everything sent to the chat gets loud in-library feedback: real read
  // progress for local files, then a confirmation. Without it the panel
  // looks dead (files land silently as composer attachments).
  const [notice, setNotice] = useState<
    | { kind: "progress"; label: string; percent: number }
    | { kind: "done"; label: string }
    | null
  >(null);
  const noticeTimerRef = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (noticeTimerRef.current) window.clearTimeout(noticeTimerRef.current);
    },
    [],
  );
  const flashDone = (label: string) => {
    if (noticeTimerRef.current) window.clearTimeout(noticeTimerRef.current);
    setNotice({ kind: "done", label });
    noticeTimerRef.current = window.setTimeout(() => setNotice(null), 3000);
  };

  // Last batch of local uploads, mirrored in the aside so the panel shows
  // where they went (they land as composer attachments, not tray references).
  const [sentFiles, setSentFiles] = useState<
    { name: string; kind: "image" | "video" | "audio" | "file" }[]
  >([]);

  const takeLocalFiles = (list: FileList | File[] | null) => {
    if (!list || list.length === 0) return;
    const files = Array.from(list);
    const label =
      files.length === 1
        ? files[0].name
        : `${files.length} ${tx("studio.mediaLibrary.files", "files")}`;
    if (noticeTimerRef.current) window.clearTimeout(noticeTimerRef.current);
    const totalBytes = files.reduce((sum, file) => sum + file.size, 0) || 1;
    const loaded = files.map(() => 0);
    let finished = 0;
    setNotice({ kind: "progress", label, percent: 0 });
    const update = () => {
      const percent = Math.min(
        100,
        Math.round((loaded.reduce((a, b) => a + b, 0) / totalBytes) * 100),
      );
      setNotice({ kind: "progress", label, percent });
    };
    files.forEach((file, index) => {
      const reader = new FileReader();
      reader.onprogress = (event) => {
        if (event.lengthComputable) {
          loaded[index] = event.loaded;
          update();
        }
      };
      reader.onloadend = () => {
        loaded[index] = file.size;
        finished += 1;
        update();
        if (finished === files.length) {
          onLocalFiles?.(files);
          setSentFiles(
            files.map((entry) => ({
              name: entry.name,
              kind: entry.type.startsWith("image/")
                ? ("image" as const)
                : entry.type.startsWith("video/")
                  ? ("video" as const)
                  : entry.type.startsWith("audio/")
                    ? ("audio" as const)
                    : ("file" as const),
            })),
          );
          flashDone(
            tx(
              "studio.mediaLibrary.uploadDone",
              "Added to chat - see the composer attachments",
            ),
          );
        }
      };
      reader.readAsArrayBuffer(file);
    });
  };

  const trayIndex = useMemo(() => {
    const map = new Map<string, number>();
    tray.forEach((item, index) => map.set(item.id, index));
    return map;
  }, [tray]);

  const toggleItem = (item: MediaTemplateItem) => {
    setTray((prev) => {
      if (prev.some((entry) => entry.id === item.id)) {
        return prev.filter((entry) => entry.id !== item.id);
      }
      if (prev.length >= MAX_MEDIA_REFERENCES) return prev;
      return [...prev, item];
    });
  };

  const addTrayToPrompt = () => {
    if (tray.length === 0) return;
    const count = tray.length;
    onUse(tray);
    setTray([]);
    flashDone(
      `${count} ${tx(
        "studio.mediaLibrary.trayAdded",
        "reference(s) added to the prompt - ready in the chat",
      )}`,
    );
  };

  const workflows = studio === "montage" ? MONTAGE_WORKFLOWS : MARKETING_WORKFLOWS;
  const workflowCards = useMemo(
    () =>
      workflows
        .map((workflow) => ({
          workflow,
          refs: resolveWorkflowRefs(workflow, items),
        }))
        .filter((entry) => entry.refs.length > 0),
    [items, workflows],
  );
  const [workflowsOpen, setWorkflowsOpen] = useState(false);
  const identity = IDENTITY[studio];
  const StudioIcon = identity.icon;

  return (
    <div className="relative flex min-h-0 flex-1 overflow-hidden bg-background">
      <aside className="flex w-[200px] shrink-0 flex-col gap-1 border-r border-border/50 px-3 py-4">
        <div
          className={cn(
            "mb-2 flex items-center gap-2 rounded-xl px-2.5 py-2",
            identity.accentSoft,
          )}
        >
          <StudioIcon className="h-3.5 w-3.5 shrink-0" strokeWidth={1.75} />
          <span className="text-[11px] font-semibold leading-tight">
            {tx(identity.labelKey, identity.labelFallback)}
          </span>
        </div>
        {studio === "montage" ? (
          <button
            type="button"
            onClick={() => setPane("dub")}
            className={cn(
              "mb-2 flex items-center justify-between rounded-full px-3 py-2 text-left text-[13px] transition-colors",
              pane === "dub"
                ? identity.accentSoft
                : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
            )}
            data-testid="media-dub-entry"
          >
            <span className="flex min-w-0 items-center gap-2">
              <Languages className="h-3.5 w-3.5 shrink-0" aria-hidden />
              {tx("studio.mediaLibrary.dub.entry", "Translate & dub")}
            </span>
            <span className="rounded-full bg-amber-500/15 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-300">
              {tx("montage.newBadge", "New")}
            </span>
          </button>
        ) : null}
        <p className="mb-1 px-2 text-[11px] text-muted-foreground">
          {tx(identity.refsKey, identity.refsFallback)}
        </p>
        {families.map((id) => (
          <button
            key={id}
            type="button"
            onClick={() => {
              setPane("library");
              setFamily(id);
            }}
            className={cn(
              "flex items-center justify-between rounded-full px-3 py-2 text-left text-[13px] transition-colors",
              pane === "library" && family === id
                ? identity.accentSoft
                : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
            )}
          >
            <span className="flex min-w-0 items-center gap-2 capitalize">
              {(() => {
                const Icon = FAMILY_ICONS[id];
                return <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden />;
              })()}
              {tx(`studio.mediaLibrary.family.${id}`, id)}
            </span>
            {pinned.has(id) ? (
              <Pin className="h-3 w-3 text-muted-foreground" aria-hidden />
            ) : null}
          </button>
        ))}
      </aside>

      <section className="flex min-w-0 flex-1 flex-col">
        {studio === "montage" && pane === "dub" ? (
          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6">
            <div className="flex items-center gap-3">
              <span
                className={cn(
                  "flex h-10 w-10 shrink-0 items-center justify-center rounded-xl",
                  identity.accentSoft,
                )}
              >
                <Languages className="h-5 w-5" strokeWidth={1.75} />
              </span>
              <div>
                <h1 className="text-[22px] font-semibold tracking-tight">
                  {tx("studio.mediaLibrary.dub.title", "Translate & dub a video")}
                </h1>
                <p className="text-[12.5px] text-muted-foreground">
                  {tx(
                    "studio.mediaLibrary.dub.subtitle",
                    "Upload a clip, pick a target language: transcription, translation, new voice, re-injected audio.",
                  )}
                </p>
              </div>
            </div>

            <input
              ref={dubVideoRef}
              type="file"
              accept="video/*"
              className="hidden"
              onChange={(event) => {
                takeLocalFiles(event.target.files);
                event.target.value = "";
              }}
            />
            <button
              type="button"
              onClick={() => dubVideoRef.current?.click()}
              className="mt-6 flex w-full flex-col items-center justify-center gap-2 rounded-2xl border border-dashed border-border/70 bg-muted/20 px-6 py-10 text-center transition-colors hover:border-foreground/40 hover:bg-muted/40"
              data-testid="media-dub-upload"
            >
              <Upload className="h-6 w-6 text-muted-foreground" strokeWidth={1.5} />
              <span className="text-[13.5px] font-medium">
                {tx("studio.mediaLibrary.dub.upload", "Upload the video to translate")}
              </span>
              <span className="text-[11.5px] text-muted-foreground">
                {tx(
                  "studio.mediaLibrary.dub.uploadHint",
                  "The file attaches to the chat. You can also paste a YouTube / page URL in the prompt.",
                )}
              </span>
            </button>

            <p className="mt-6 text-[12px] font-medium text-muted-foreground">
              {tx(
                "studio.mediaLibrary.dub.modeTitle",
                "What to produce - check one or both",
              )}
            </p>
            <div className="mt-2.5 grid grid-cols-1 gap-2 sm:grid-cols-2">
              {(
                [
                  {
                    key: "voice" as const,
                    checked: dubVoice,
                    toggle: () => setDubVoice((v) => !v),
                    label: tx("studio.mediaLibrary.dub.modeVoice", "New voice (dub)"),
                    hint: tx(
                      "studio.mediaLibrary.dub.modeVoiceHint",
                      "Audio replaced, original kept as a quiet bed.",
                    ),
                  },
                  {
                    key: "subs" as const,
                    checked: dubSubs,
                    toggle: () => setDubSubs((v) => !v),
                    label: tx("studio.mediaLibrary.dub.modeSubs", "Translated subtitles"),
                    hint: tx(
                      "studio.mediaLibrary.dub.modeSubsHint",
                      "Translated subtitles burned into the video.",
                    ),
                  },
                ]
              ).map((mode) => (
                <button
                  key={mode.key}
                  type="button"
                  onClick={mode.toggle}
                  aria-pressed={mode.checked}
                  className={cn(
                    "flex items-start gap-2.5 rounded-2xl border px-3.5 py-3 text-left transition-all",
                    mode.checked
                      ? cn("shadow-sm", identity.accentBorder, identity.accentSoft)
                      : "border-border/55 hover:border-foreground/30",
                  )}
                  data-testid={`media-dub-mode-${mode.key}`}
                >
                  <span
                    className={cn(
                      "mt-0.5 flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-[5px] border",
                      mode.checked
                        ? cn("border-transparent text-white", identity.accentDot)
                        : "border-border",
                    )}
                  >
                    {mode.checked ? <Check className="h-3 w-3" strokeWidth={3} /> : null}
                  </span>
                  <span className="min-w-0">
                    <span className="block text-[13px] font-medium">{mode.label}</span>
                    <span className="block text-[11px] text-muted-foreground">
                      {mode.hint}
                    </span>
                  </span>
                </button>
              ))}
            </div>

            <div className="mt-6 grid grid-cols-1 gap-3 sm:grid-cols-2">
              <DubLanguageField
                label={tx("studio.mediaLibrary.dub.sourceLabel", "Video language (spoken)")}
                value={dubSource}
                onChange={setDubSource}
                placeholder={tx(
                  "studio.mediaLibrary.dub.sourceAuto",
                  "Auto-detect (or type it)",
                )}
                testId="media-dub-source"
              />
              <DubLanguageField
                label={tx("studio.mediaLibrary.dub.targetLabel", "Translate to")}
                value={dubTarget}
                onChange={setDubTarget}
                placeholder={tx(
                  "studio.mediaLibrary.dub.targetPlaceholder",
                  "Type or pick a language",
                )}
                testId="media-dub-target"
              />
            </div>

            {dubVoice ? (
              <div className="mt-6">
                <p className="text-[12px] font-medium text-muted-foreground">
                  {tx("studio.mediaLibrary.dub.cloneTitle", "Voice for the dub")}
                </p>
                <div className="mt-2.5 grid grid-cols-1 gap-2">
                  {(
                    [
                      {
                        key: "catalog" as const,
                        label: tx("studio.mediaLibrary.dub.cloneCatalog", "Model voice"),
                        hint: tx(
                          "studio.mediaLibrary.dub.cloneCatalogHint",
                          "Use the voice already picked in Settings. Works with every TTS.",
                        ),
                      },
                      {
                        key: "source" as const,
                        label: tx("studio.mediaLibrary.dub.cloneSource", "Voice of this video"),
                        hint: tx(
                          "studio.mediaLibrary.dub.cloneSourceHint",
                          "Clone the speaker from a short clip. Needs Fish Audio or ElevenLabs.",
                        ),
                      },
                      {
                        key: "custom" as const,
                        label: tx("studio.mediaLibrary.dub.cloneCustom", "My sample / voice id"),
                        hint: tx(
                          "studio.mediaLibrary.dub.cloneCustomHint",
                          "Attach a wav, or type a provider voice id. Same models as above.",
                        ),
                      },
                    ]
                  ).map((option) => (
                    <button
                      key={option.key}
                      type="button"
                      onClick={() => setDubClone(option.key)}
                      aria-pressed={dubClone === option.key}
                      className={cn(
                        "flex items-start gap-2.5 rounded-2xl border px-3.5 py-3 text-left transition-all",
                        dubClone === option.key
                          ? cn("shadow-sm", identity.accentBorder, identity.accentSoft)
                          : "border-border/55 hover:border-foreground/30",
                      )}
                      data-testid={`media-dub-clone-${option.key}`}
                    >
                      <span
                        className={cn(
                          "mt-0.5 flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full border",
                          dubClone === option.key
                            ? cn("border-transparent text-white", identity.accentDot)
                            : "border-border",
                        )}
                      >
                        {dubClone === option.key ? (
                          <Check className="h-3 w-3" strokeWidth={3} />
                        ) : null}
                      </span>
                      <span className="min-w-0">
                        <span className="block text-[13px] font-medium">{option.label}</span>
                        <span className="block text-[11px] text-muted-foreground">
                          {option.hint}
                        </span>
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            ) : null}

            <div className="mt-6 flex items-center gap-3">
              <button
                type="button"
                disabled={!dubTarget.trim() || (!dubVoice && !dubSubs)}
                onClick={() =>
                  onSeed?.(
                    dubSeedText(
                      { voice: dubVoice, subs: dubSubs },
                      dubSource,
                      dubTarget,
                      dubClone,
                    ),
                    { replace: true },
                  )
                }
                className={cn(
                  "rounded-full px-5 py-2 text-[13px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40",
                  identity.accentSolid,
                )}
                data-testid="media-dub-fill"
              >
                {tx("studio.mediaLibrary.dub.fill", "Fill the prompt")}
              </button>
              <span className="text-[11.5px] text-muted-foreground">
                {!dubVoice && !dubSubs
                  ? tx(
                      "studio.mediaLibrary.dub.needMode",
                      "Check at least voice or subtitles.",
                    )
                  : !dubTarget.trim()
                    ? tx(
                        "studio.mediaLibrary.dub.needTarget",
                        "Pick or type the target language.",
                      )
                    : tx(
                        "studio.mediaLibrary.dub.ready",
                        "The prompt lands in the chat - attach or send.",
                      )}
              </span>
            </div>
          </div>
        ) : (
          <>
        <div className="flex shrink-0 items-center gap-2 border-b border-border/40 px-5 py-3">
          <div className="flex rounded-full bg-muted p-0.5">
            {(["image", "video"] as const).map((value) => (
              <button
                key={value}
                type="button"
                onClick={() => setKind(value)}
                className={cn(
                  "rounded-full px-3.5 py-1 text-[12px] font-medium",
                  kind === value ? identity.kindActive : "text-muted-foreground",
                )}
              >
                {tx(`studio.mediaLibrary.kind.${value}`, value === "image" ? "Image" : "Video")}
              </button>
            ))}
          </div>
          {workflowCards.length > 0 ? (
            <div className="relative">
              <button
                type="button"
                onClick={() => setWorkflowsOpen((open) => !open)}
                className={cn(
                  "flex h-8 items-center gap-1.5 rounded-full px-3 text-[12px] font-medium transition-colors",
                  identity.accentSoft,
                )}
                data-testid="media-workflows-toggle"
              >
                <Rocket className="h-3.5 w-3.5" strokeWidth={1.75} />
                {tx("studio.mediaLibrary.gettingStarted.title", "Getting started")}
              </button>
              {workflowsOpen ? (
                <div className="absolute left-0 top-full z-20 mt-2 w-[340px] rounded-2xl border border-border/60 bg-background p-2 shadow-lg">
                  <p className="px-2 pb-1.5 pt-1 text-[11px] text-muted-foreground">
                    {tx(
                      "studio.mediaLibrary.gettingStarted.subtitle",
                      "One click: references + prompt, ready to send.",
                    )}
                  </p>
                  {workflowCards.map(({ workflow, refs }) => {
                    const thumb = dedupeByBasePhoto(refs)[0];
                    return (
                      <button
                        key={workflow.id}
                        type="button"
                        onClick={() => {
                          setWorkflowsOpen(false);
                          onWorkflow?.(workflow.prompt, refs);
                          flashDone(
                            tx(
                              "studio.mediaLibrary.workflowReady",
                              "Workflow ready in the chat - prompt and references attached",
                            ),
                          );
                        }}
                        className="flex w-full items-center gap-3 rounded-xl px-2 py-2 text-left hover:bg-muted/60"
                        data-testid={`media-workflow-${workflow.id}`}
                      >
                        <img
                          src={thumb.preview_url}
                          alt=""
                          loading="lazy"
                          className="h-11 w-11 shrink-0 rounded-lg object-cover"
                        />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[12.5px] font-medium leading-tight">
                            {tx(
                              `studio.mediaLibrary.gettingStarted.${workflow.id}.title`,
                              workflow.title,
                            )}
                          </span>
                          <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">
                            {tx(
                              `studio.mediaLibrary.gettingStarted.${workflow.id}.description`,
                              workflow.description,
                            )}
                          </span>
                        </span>
                        <span
                          className={cn(
                            "shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-medium",
                            identity.accentSoft,
                          )}
                        >
                          {refs.length}{" "}
                          {tx("studio.mediaLibrary.gettingStarted.refs", "refs")}
                        </span>
                      </button>
                    );
                  })}
                </div>
              ) : null}
            </div>
          ) : null}
          <label className="relative min-w-0 flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={tx("studio.mediaLibrary.search", "Search")}
              className="h-9 w-full rounded-full border border-border/50 bg-muted/30 pl-9 pr-9 text-[13px] outline-none focus:border-foreground/30"
            />
            <ImageIcon className="pointer-events-none absolute right-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          </label>
          <div className="relative">
            <button
              type="button"
              onClick={() => setFiltersOpen((open) => !open)}
              className={cn(
                "rounded-full p-2 hover:bg-muted",
                filtersActive || filtersOpen
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground",
              )}
              aria-label={tx("studio.mediaLibrary.filters", "Filters")}
              data-testid="media-filters-toggle"
            >
              <SlidersHorizontal className="h-4 w-4" />
              {filtersActive ? (
                <span className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-foreground" />
              ) : null}
            </button>
            {filtersOpen ? (
              <div className="absolute right-0 top-full z-20 mt-2 w-56 rounded-2xl border border-border/60 bg-background p-3 shadow-lg">
                <p className="mb-1.5 text-[11px] font-medium text-muted-foreground">
                  {tx("studio.mediaLibrary.filterFormat", "Format")}
                </p>
                <div className="flex flex-wrap gap-1.5">
                  <button
                    type="button"
                    onClick={() => setFormatFilter(null)}
                    className={cn(
                      "rounded-full px-2.5 py-1 text-[11px]",
                      formatFilter === null
                        ? "bg-foreground text-background"
                        : "border border-border/60 text-muted-foreground hover:bg-muted",
                    )}
                  >
                    {tx("studio.mediaLibrary.filterAll", "All")}
                  </button>
                  {["16:9", "9:16", "1:1", "4:5", "21:9"].map((fmt) => (
                    <button
                      key={fmt}
                      type="button"
                      onClick={() =>
                        setFormatFilter((current) => (current === fmt ? null : fmt))
                      }
                      className={cn(
                        "rounded-full px-2.5 py-1 text-[11px] tabular-nums",
                        formatFilter === fmt
                          ? "bg-foreground text-background"
                          : "border border-border/60 text-muted-foreground hover:bg-muted",
                      )}
                    >
                      {fmt}
                    </button>
                  ))}
                </div>
                <label className="mt-3 flex cursor-pointer items-center justify-between text-[12px]">
                  {tx("studio.mediaLibrary.filterPremium", "Premium only")}
                  <input
                    type="checkbox"
                    checked={premiumOnly}
                    onChange={(event) => setPremiumOnly(event.target.checked)}
                    className="h-3.5 w-3.5 accent-foreground"
                  />
                </label>
                {filtersActive ? (
                  <button
                    type="button"
                    onClick={() => {
                      setFormatFilter(null);
                      setPremiumOnly(false);
                    }}
                    className="mt-3 w-full rounded-full border border-border/60 py-1 text-[11px] text-muted-foreground hover:bg-muted"
                  >
                    {tx("studio.mediaLibrary.filterReset", "Reset filters")}
                  </button>
                ) : null}
              </div>
            ) : null}
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
          <h1 className="flex items-center gap-2.5 text-[28px] font-semibold tracking-tight capitalize">
            <span className={cn("h-2 w-2 rounded-full", identity.accentDot)} />
            {tx(`studio.mediaLibrary.family.${family}`, family)}
          </h1>
          {kind === "image" ? (
            <div className="mt-3 flex gap-5 text-[13px]">
              {TABS.map((id) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setTab(id)}
                  className={cn(
                    "pb-1 capitalize",
                    tab === id
                      ? "border-b-2 border-foreground font-medium text-foreground"
                      : "text-muted-foreground",
                  )}
                >
                  {tx(`studio.mediaLibrary.tab.${id}`, id)}
                </button>
              ))}
            </div>
          ) : null}
          <div className="mt-4 flex flex-wrap gap-2">
            {TAGS.map((id) => (
              <button
                key={id}
                type="button"
                onClick={() => setTag((current) => (current === id ? null : id))}
                className={cn(
                  "rounded-full px-3 py-1 text-[12px] capitalize",
                  tag === id
                    ? identity.accentSolid
                    : "text-muted-foreground hover:bg-muted",
                )}
              >
                {tx(`studio.mediaLibrary.tag.${id}`, id)}
              </button>
            ))}
          </div>

          <div className="mt-5 columns-2 gap-3 md:columns-3 xl:columns-4">
            <AnimatePresence>
              {visible.map((item) => {
                const picked = trayIndex.get(item.id);
                const isPicked = picked !== undefined;
                const showMotion = item.kind === "video" && hoveredId === item.id;
                return (
                  <motion.button
                    key={item.id}
                    type="button"
                    layout
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0 }}
                    transition={spring}
                    onClick={() => toggleItem(item)}
                    onMouseEnter={() => setHoveredId(item.id)}
                    onMouseLeave={() =>
                      setHoveredId((current) => (current === item.id ? null : current))
                    }
                    className={cn(
                      "mb-3 block w-full break-inside-avoid overflow-hidden rounded-2xl border text-left",
                      isPicked
                        ? identity.accentBorder
                        : "border-transparent hover:border-border",
                    )}
                  >
                    <span className="relative block">
                      {showMotion ? (
                        <video
                          src={item.url}
                          poster={item.preview_url}
                          muted
                          loop
                          autoPlay
                          playsInline
                          className={cn(
                            "w-full object-cover",
                            FORMAT_ASPECT[item.format] ?? "aspect-video",
                          )}
                        />
                      ) : (
                        <img
                          src={item.preview_url}
                          alt={item.title}
                          loading="lazy"
                          className={cn(
                            "w-full object-cover",
                            FORMAT_ASPECT[item.format] ?? "aspect-video",
                          )}
                        />
                      )}
                      {item.kind === "video" ? (
                        studio === "montage" ? (
                          <Clapperboard className="absolute left-2 top-2 h-4 w-4 text-white drop-shadow" />
                        ) : (
                          <Video className="absolute left-2 top-2 h-4 w-4 text-white drop-shadow" />
                        )
                      ) : null}
                      {item.premium ? (
                        <ShoppingBag className="absolute bottom-2 left-2 h-4 w-4 text-white drop-shadow" />
                      ) : null}
                      <span className="absolute right-2 top-2 rounded-full bg-black/55 px-2 py-0.5 text-[10px] text-white">
                        {item.format}
                      </span>
                      {isPicked ? (
                        <span
                          className={cn(
                            "absolute bottom-2 right-2 flex h-5 min-w-5 items-center justify-center gap-0.5 rounded-full px-1 text-[10px] font-semibold",
                            identity.accentSolid,
                          )}
                        >
                          <Check className="h-3 w-3" strokeWidth={2.5} />
                          {(picked ?? 0) + 1}
                        </span>
                      ) : null}
                    </span>
                  </motion.button>
                );
              })}
            </AnimatePresence>
          </div>
          {visible.length === 0 ? (
            <p className="mt-10 text-center text-[13px] text-muted-foreground">
              {tx("studio.mediaLibrary.empty", "No templates in this filter.")}
            </p>
          ) : null}
        </div>
          </>
        )}
      </section>

      {/* The reference tray is pointless in the dub pane: the video attaches
          straight to the chat, no template references are involved. */}
      {pane === "dub" ? null : (
      <aside className="flex w-[200px] shrink-0 flex-col gap-1.5 border-l border-border/50 px-3 py-3">
        <div className="flex items-center justify-between px-1">
          <p className="text-[11px] text-muted-foreground">
            {tx("studio.mediaLibrary.trayTitle", "References")}
          </p>
          <span className="text-[11px] tabular-nums text-muted-foreground">
            {tray.length}/{MAX_MEDIA_REFERENCES}
          </span>
        </div>
        <div className="grid grid-cols-2 gap-1.5">
          {tray.map((item) => (
            <div
              key={item.id}
              className="group relative overflow-hidden rounded-lg border border-border/50"
            >
              <img
                src={item.preview_url}
                alt={item.title}
                className="aspect-square w-full object-cover"
              />
              {item.kind === "video" ? (
                <Video className="absolute left-1 top-1 h-3 w-3 text-white drop-shadow" />
              ) : null}
              <button
                type="button"
                onClick={() => toggleItem(item)}
                className="absolute right-1 top-1 rounded-full bg-black/60 p-0.5 text-white opacity-0 transition-opacity group-hover:opacity-100"
                aria-label={tx("studio.mediaLibrary.trayRemove", "Remove reference")}
              >
                <X className="h-3 w-3" />
              </button>
            </div>
          ))}
          {tray.length === 0 ? (
            <div className="col-span-2 flex h-[72px] items-center justify-center rounded-lg border border-dashed border-border/70 bg-muted/20 px-2 text-center text-[11px] leading-snug text-muted-foreground">
              {tx(
                "studio.mediaLibrary.trayEmpty",
                "Click media to build your reference set.",
              )}
            </div>
          ) : null}
        </div>
        {tray.length > 0 ? (
          <div className="flex flex-col gap-1.5">
            <button
              type="button"
              onClick={addTrayToPrompt}
              className={cn(
                "flex h-8 items-center justify-center gap-1.5 rounded-full px-3 text-[12px] font-medium",
                identity.accentSolid,
              )}
              data-testid="media-tray-add"
            >
              <Check className="h-3.5 w-3.5" strokeWidth={2} />
              {tx("studio.mediaLibrary.trayAdd", "Add")} {tray.length}
            </button>
            <button
              type="button"
              onClick={() => setTray([])}
              className="flex h-8 items-center justify-center rounded-full border border-border/60 px-3 text-[12px] hover:bg-muted/50"
            >
              {tx("studio.mediaLibrary.trayClear", "Clear all")}
            </button>
          </div>
        ) : null}

        {sentFiles.length > 0 ? (
          <div className="mt-2 flex flex-col gap-1" data-testid="media-sent-files">
            <div className="flex items-center justify-between px-1">
              <p className="text-[11px] text-muted-foreground">
                {tx("studio.mediaLibrary.sentTitle", "Attached to chat")}
              </p>
              <button
                type="button"
                onClick={() => setSentFiles([])}
                className="rounded-full p-0.5 text-muted-foreground hover:text-foreground"
                aria-label={tx("studio.mediaLibrary.sentDismiss", "Dismiss")}
              >
                <X className="h-3 w-3" />
              </button>
            </div>
            {sentFiles.map((entry, index) => {
              const FileIcon =
                entry.kind === "image"
                  ? ImageIcon
                  : entry.kind === "video"
                    ? Video
                    : entry.kind === "audio"
                      ? Mic
                      : Square;
              return (
                <div
                  key={`${entry.name}-${index}`}
                  className="flex items-center gap-2 rounded-lg border border-border/50 bg-muted/25 px-2 py-1.5"
                >
                  <FileIcon
                    className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
                    strokeWidth={1.75}
                  />
                  <span className="min-w-0 truncate text-[11px]">{entry.name}</span>
                  <Check
                    className={cn("ml-auto h-3 w-3 shrink-0", identity.accentText)}
                    strokeWidth={2.5}
                  />
                </div>
              );
            })}
            <p className="px-1 text-[10px] leading-snug text-muted-foreground">
              {tx(
                "studio.mediaLibrary.sentHint",
                "In the chat composer - send your prompt to use them.",
              )}
            </p>
          </div>
        ) : null}

        <div className="mt-auto flex flex-col gap-1.5">
          <div
            className="flex h-[56px] items-center justify-center rounded-xl border border-dashed border-border/70 bg-muted/25 px-3 text-center text-[11px] leading-snug text-muted-foreground"
            onDragOver={(event) => {
              event.preventDefault();
              event.stopPropagation();
            }}
            onDrop={(event) => {
              event.preventDefault();
              event.stopPropagation();
              takeLocalFiles(event.dataTransfer.files);
            }}
          >
            {tx("studio.mediaLibrary.drop", "Drop a file or import media from disk.")}
          </div>
          <input
            ref={fileRef}
            type="file"
            accept="image/*,video/*,audio/*"
            multiple
            className="hidden"
            onChange={(event) => {
              takeLocalFiles(event.target.files);
              event.target.value = "";
            }}
          />
          <input
            ref={imageRef}
            type="file"
            accept="image/*"
            multiple
            className="hidden"
            onChange={(event) => {
              takeLocalFiles(event.target.files);
              event.target.value = "";
            }}
          />
          <input
            ref={videoRef}
            type="file"
            accept="video/*"
            className="hidden"
            onChange={(event) => {
              takeLocalFiles(event.target.files);
              event.target.value = "";
            }}
          />
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            className="flex h-8 items-center justify-center gap-1.5 rounded-full bg-foreground px-3 text-[12px] font-medium text-background"
          >
            <Upload className="h-3.5 w-3.5" strokeWidth={1.75} />
            {tx("studio.mediaLibrary.upload", "Upload media")}
          </button>
          <SideAction
            icon={ImageIcon}
            label={tx("studio.mediaLibrary.uploadImage", "Upload image")}
            onClick={() => imageRef.current?.click()}
          />
          <SideAction
            icon={Video}
            label={tx("studio.mediaLibrary.importVideo", "Import video")}
            onClick={() => videoRef.current?.click()}
          />
          <SideAction
            icon={Mic}
            label={tx("studio.mediaLibrary.addAudio", "Add voice or music")}
            onClick={() =>
              onSeed?.(
                studio === "montage"
                  ? "/montage Generate a voice or music bed for the selected template.\n\n"
                  : "/campaign Generate a voice or music bed for the selected template.\n\n",
              )
            }
          />
        </div>
      </aside>
      )}

      <AnimatePresence>
        {notice ? (
          <motion.div
            key="library-notice"
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 8 }}
            transition={spring}
            className="pointer-events-none absolute bottom-4 left-1/2 z-30 w-80 -translate-x-1/2 rounded-2xl border border-border/60 bg-background/95 p-3 shadow-lg backdrop-blur"
            data-testid="media-library-notice"
          >
            {notice.kind === "progress" ? (
              <>
                <div className="flex items-center justify-between gap-2 text-[11.5px]">
                  <span className="min-w-0 truncate">{notice.label}</span>
                  <span className="shrink-0 tabular-nums text-muted-foreground">
                    {notice.percent}%
                  </span>
                </div>
                <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-muted">
                  <div
                    className={cn(
                      "h-full rounded-full transition-[width] duration-150",
                      identity.accentDot,
                    )}
                    style={{ width: `${notice.percent}%` }}
                  />
                </div>
              </>
            ) : (
              <div className="flex items-center gap-2 text-[12px]">
                <span
                  className={cn(
                    "flex h-5 w-5 shrink-0 items-center justify-center rounded-full",
                    identity.accentSoft,
                  )}
                >
                  <Check className="h-3 w-3" strokeWidth={2.5} />
                </span>
                <span className="min-w-0">{notice.label}</span>
              </div>
            )}
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

function SideAction({
  icon: Icon,
  label,
  onClick,
}: {
  icon: typeof Square;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex h-8 items-center gap-2 rounded-full border border-border/60 px-3 text-left text-[12px] hover:bg-muted/50"
    >
      <Icon className="h-3.5 w-3.5 text-muted-foreground" strokeWidth={1.75} />
      {label}
    </button>
  );
}
