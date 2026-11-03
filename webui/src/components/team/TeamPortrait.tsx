/**
 * A drawn human face for a teammate (avatar "person"): skin, hair, beard and
 * glasses per teammate, the shirt in its color. The default team has a face
 * each; any other teammate gets one from its id and gender, the same every
 * time. A real photo, when uploaded in Settings > Team, replaces it.
 */
export type HairStyle = "short" | "side" | "buzz" | "curly" | "long" | "bob" | "bun" | "ponytail" | "waves" | "afro";

export interface PortraitSpec {
  skin: string;
  hair: string;
  hairStyle: HairStyle;
  beard?: boolean;
  glasses?: boolean;
}

const DEFAULT_PORTRAITS: Record<string, PortraitSpec> = {
  michel: { skin: "#e0ac69", hair: "#5b4636", hairStyle: "side", beard: true, glasses: true },
  mark: { skin: "#f1c27d", hair: "#6b4f3a", hairStyle: "short" },
  salma: { skin: "#c68642", hair: "#2b1d16", hairStyle: "long" },
  ethan: { skin: "#ffdbac", hair: "#3b2f2f", hairStyle: "buzz" },
  grace: { skin: "#f1c27d", hair: "#a0522d", hairStyle: "bob", glasses: true },
  karim: { skin: "#c68642", hair: "#1f1a17", hairStyle: "curly", beard: true },
  olivia: { skin: "#ffdbac", hair: "#d4a373", hairStyle: "ponytail" },
  lea: { skin: "#f6d5b5", hair: "#8d5524", hairStyle: "waves" },
  jack: { skin: "#e0ac69", hair: "#2e2a26", hairStyle: "side" },
  ava: { skin: "#8d5524", hair: "#1b1410", hairStyle: "afro" },
  nora: { skin: "#f1c27d", hair: "#3b2a20", hairStyle: "bun" },
  ryan: { skin: "#ffdbac", hair: "#c19a6b", hairStyle: "short" },
  daniel: { skin: "#8d5524", hair: "#1b1410", hairStyle: "buzz", beard: true },
  lucas: { skin: "#e0ac69", hair: "#4a3728", hairStyle: "curly", glasses: true },
};

const SKINS = ["#ffdbac", "#f6d5b5", "#f1c27d", "#e0ac69", "#c68642", "#8d5524", "#5c3a21"];
const HAIRS = ["#1b1410", "#2b1d16", "#3b2a20", "#4a3728", "#6b4f3a", "#a0522d", "#d4a373", "#5b4636"];
const FEMALE_STYLES: HairStyle[] = ["long", "bob", "bun", "ponytail", "waves", "afro"];
const MALE_STYLES: HairStyle[] = ["short", "side", "buzz", "curly"];
const ANY_STYLES: HairStyle[] = ["short", "bob", "curly", "side", "waves"];

function hash(text: string): number {
  let value = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    value ^= text.charCodeAt(index);
    value = Math.imul(value, 16777619);
  }
  return value >>> 0;
}

/** The face of teammate *seed* (its id): the default team's, or one made from it. */
export function portraitSpec(seed: string, gender?: string): PortraitSpec {
  const known = DEFAULT_PORTRAITS[seed];
  if (known) return known;
  const value = hash(seed || "teammate");
  const styles = gender === "female" ? FEMALE_STYLES : gender === "male" ? MALE_STYLES : ANY_STYLES;
  return {
    skin: SKINS[value % SKINS.length],
    hair: HAIRS[(value >>> 5) % HAIRS.length],
    hairStyle: styles[(value >>> 9) % styles.length],
    beard: gender === "male" && (value >>> 13) % 3 === 0,
    glasses: (value >>> 17) % 4 === 0,
  };
}

/** Hair drawn behind the head (long styles fall to the shoulders). */
function HairBack({ style, color }: { style: HairStyle; color: string }) {
  switch (style) {
    case "long":
    case "waves":
      return <path d="M9.3 14.4C8.9 8.6 12.1 6.5 16 6.5s7.1 2.1 6.7 7.9l.6 10.1c-1.7 1.1-3.6 1.2-4.7.3l-.2-5.6h-4.8l-.2 5.6c-1.1.9-3 .8-4.7-.3z" fill={color} />;
    case "bob":
      return <path d="M9.5 14.2C9.1 8.8 12.1 6.7 16 6.7s6.9 2.1 6.5 7.5l.2 5.5c-1.2 1-2.5 1-3.3.4h-6.8c-.8.6-2.1.6-3.3-.4z" fill={color} />;
    case "ponytail":
      return <path d="M20.4 9.4c4.4.7 4.9 6.6 3.1 11.2-.6-3-1.5-6.1-3.3-8.1z" fill={color} />;
    case "afro":
      return <circle cx="16" cy="11.6" r="8.6" fill={color} />;
    default:
      return null;
  }
}

/** Hair drawn over the head: the hairline and fringe. */
function HairFront({ style, color }: { style: HairStyle; color: string }) {
  switch (style) {
    case "short":
      return <path d="M10.2 13.4C9.8 9 12.4 7.2 16 7.2s6.2 1.8 5.8 6.2c-.8-2.2-2.6-3.2-5.8-3.2s-5 1-5.8 3.2z" fill={color} />;
    case "side":
      return <path d="M10.2 13.6c-.6-4.8 2.4-6.7 6.2-6.6 3.6.1 6 2.2 5.4 6.2-.6-2.2-2.2-3.3-4.6-3.3-1.6 1.3-4.6 1.7-7 3.7z" fill={color} />;
    case "buzz":
      return <path d="M10.6 12.4c0-3.4 2.3-4.6 5.4-4.6s5.4 1.2 5.4 4.6c-1-1.6-2.8-2.4-5.4-2.4s-4.4.8-5.4 2.4z" fill={color} opacity=".88" />;
    case "curly":
      return (
        <g fill={color}>
          <circle cx="11.3" cy="10.6" r="2.1" />
          <circle cx="13.5" cy="8.7" r="2.2" />
          <circle cx="16.1" cy="8.1" r="2.3" />
          <circle cx="18.7" cy="8.7" r="2.2" />
          <circle cx="20.8" cy="10.6" r="2.1" />
        </g>
      );
    case "long":
      return <path d="M10.4 13.2c.2-3.8 2.6-5.2 5.6-5.2s5.4 1.4 5.6 5.2c-2-2-3.8-2.8-6.4-2.6-1.8.2-3.4 1-4.8 2.6z" fill={color} />;
    case "waves":
      return <path d="M10.4 13.4c-.2-4 2.2-5.8 5.8-5.8 3.4 0 5.8 2 5.4 6-1.2-2.8-4.2-4-7-3.2-1.8.6-3.2 1.6-4.2 3z" fill={color} />;
    case "bob":
      return <path d="M10.3 12c.2-3 2.6-4.4 5.7-4.4s5.5 1.4 5.7 4.4z" fill={color} />;
    case "bun":
      return (
        <g fill={color}>
          <circle cx="16" cy="5.6" r="2.6" />
          <path d="M10.3 13.2C9.9 9 12.5 7.3 16 7.3s6.1 1.7 5.7 5.9c-.9-2-2.7-3-5.7-3s-4.8 1-5.7 3z" />
        </g>
      );
    case "ponytail":
      return <path d="M10.3 13.2C9.9 9 12.5 7.3 16 7.3s6.1 1.7 5.7 5.9c-1.1-1.9-3.1-2.9-5.9-2.9-2.6 0-4.5.9-5.5 2.9z" fill={color} />;
    case "afro":
      return <path d="M10.6 13c.2-2.8 2.3-4.3 5.4-4.3s5.2 1.5 5.4 4.3c-1.4-1.2-3.2-1.8-5.4-1.8s-4 .6-5.4 1.8z" fill={color} />;
    default:
      return null;
  }
}

export function TeamPortrait({ seed, gender, tint }: { seed: string; gender?: string; tint: string }) {
  const face = portraitSpec(seed, gender);
  const ink = "#1f2937";
  return (
    <>
      <rect width="32" height="32" fill={`${tint}2e`} />
      <HairBack style={face.hairStyle} color={face.hair} />
      <path d="M3.5 33C4 26.2 9 23 16 23s12 3.2 12.5 10z" fill={tint} />
      <path d="M13.2 23.2 16 26.4l2.8-3.2" stroke="#ffffff" strokeOpacity=".55" strokeWidth="1" fill="none" strokeLinejoin="round" />
      <rect x="13.6" y="18.6" width="4.8" height="5" rx="2" fill={face.skin} />
      <rect x="13.6" y="18.6" width="4.8" height="2.2" rx="1" fill="#000000" opacity=".08" />
      <ellipse cx="10.4" cy="14.6" rx="1.1" ry="1.5" fill={face.skin} />
      <ellipse cx="21.6" cy="14.6" rx="1.1" ry="1.5" fill={face.skin} />
      <ellipse cx="16" cy="14.2" rx="5.6" ry="6.4" fill={face.skin} />
      {face.beard ? (
        <path d="M10.6 15.6c.2 4 2.6 5.8 5.4 5.8s5.2-1.8 5.4-5.8c-.8 2-2.8 2.8-5.4 2.8s-4.6-.8-5.4-2.8z" fill={face.hair} opacity=".92" />
      ) : null}
      <HairFront style={face.hairStyle} color={face.hair} />
      <path d="M12.5 12.7q1.2-.7 2.4 0M17.1 12.7q1.2-.7 2.4 0" stroke={face.hair} strokeWidth=".7" strokeLinecap="round" fill="none" />
      <circle cx="13.7" cy="14.6" r=".75" fill={ink} />
      <circle cx="18.3" cy="14.6" r=".75" fill={ink} />
      {face.glasses ? (
        <g stroke={ink} strokeWidth=".55" fill="none">
          <circle cx="13.7" cy="14.6" r="1.75" />
          <circle cx="18.3" cy="14.6" r="1.75" />
          <path d="M15.45 14.5h1.1" />
        </g>
      ) : null}
      <path
        d="M14.4 17.7q1.6 1.2 3.2 0"
        stroke={face.beard ? "#f8e1d0" : "#9a4b3c"}
        strokeWidth=".75"
        strokeLinecap="round"
        fill="none"
      />
    </>
  );
}
