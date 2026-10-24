// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Live's hands on the interface: the voice opens a page, reads what is on
 * screen and points at a control, pressing it when that only opens or shows
 * something. Anything that deletes, pays, pushes code, changes keys or signs
 * out is pointed at and left to the user (useLiveVoice tools open_in_app,
 * screen_controls, show_control).
 *
 * Controls are found by what the user sees (label, aria-label, title), so the
 * whole app works without annotating it. ``data-tour-sensitive`` marks a
 * control as never pressed by the voice.
 */

/** ``{ place }``: App.tsx opens it (a view, or a Settings section). */
export const TOUR_OPEN_EVENT = "navin:tour-open";

export const TOUR_PLACES = [
  "chat", "code",
  "settings/overview", "settings/account", "settings/providers", "settings/models", "settings/voice",
  "settings/image", "settings/video", "settings/appearance", "settings/studio", "settings/browser",
  "settings/computer", "settings/execution", "settings/tools", "settings/channels", "settings/apps",
  "settings/automations", "settings/skills", "settings/templates", "settings/runtime", "settings/sites", "settings/git", "settings/advanced",
  "settings/about",
  "tenders", "career", "marketing", "leads", "crm", "trading", "meeting", "notes", "montage", "content",
  "seo", "ads", "scraping", "risklens", "ops",
] as const;
export type TourPlace = (typeof TOUR_PLACES)[number];

const CONTROLS = [
  "button", "a[href]", "[role=button]", "[role=tab]", "[role=menuitem]", "[role=menuitemradio]",
  "[role=switch]", "[role=checkbox]", "[role=radio]", "[role=option]", "input:not([type=hidden])", "select", "textarea",
].join(",");

// Words of a control the voice must never press, in the three app languages.
const SENSITIVE = [
  "delete", "remove", "erase", "uninstall", "revoke", "reset", "wipe", "destroy", "discard",
  "log out", "logout", "sign out", "disconnect", "pay", "checkout", "purchase", "buy", "subscribe",
  "upgrade", "cancel subscription", "push", "force", "api key", "secret", "password", "token",
  "supprimer", "effacer", "retirer", "désinstaller", "révoquer", "réinitialiser", "déconnexion",
  "se déconnecter", "payer", "acheter", "s'abonner", "abonnement", "pousser", "clé", "mot de passe",
  "حذف", "إزالة", "مسح", "إلغاء التثبيت", "إبطال", "إعادة تعيين", "تسجيل الخروج", "دفع", "شراء", "اشتراك", "مفتاح", "كلمة المرور",
];

const HIGHLIGHT_MS = 4000;
const CLICK_DELAY_MS = 900;

function normalize(text: string): string {
  return text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/\s+/g, " ").trim();
}

function labelOf(el: HTMLElement): string {
  const aria = el.getAttribute("aria-label") || el.getAttribute("title") || "";
  const labelled = el.getAttribute("aria-labelledby");
  const fromId = labelled ? document.getElementById(labelled)?.textContent ?? "" : "";
  const placeholder = el.getAttribute("placeholder") || "";
  const text = (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
  return (aria || fromId || text || placeholder).slice(0, 80);
}

function visible(el: HTMLElement): boolean {
  const rect = el.getBoundingClientRect();
  if (rect.width < 4 || rect.height < 4) return false;
  const style = window.getComputedStyle(el);
  if (style.visibility === "hidden" || style.display === "none" || style.pointerEvents === "none") return false;
  // Inside an invisible layer (the chat column behind another view).
  return !el.closest(".invisible, [aria-hidden=true], [inert]");
}

function isSensitive(el: HTMLElement, label: string): boolean {
  if (el.closest("[data-tour-sensitive]")) return true;
  if (/\bdestructive\b/.test(el.className && typeof el.className === "string" ? el.className : "")) return true;
  if (el.tagName === "INPUT" && (el as HTMLInputElement).type === "password") return true;
  const words = normalize(label);
  return SENSITIVE.some((word) => words.includes(normalize(word)));
}

interface Control {
  el: HTMLElement;
  label: string;
  kind: string;
  sensitive: boolean;
}

function controls(): Control[] {
  const seen = new Set<HTMLElement>();
  const out: Control[] = [];
  for (const node of Array.from(document.querySelectorAll<HTMLElement>(CONTROLS))) {
    if (seen.has(node) || !visible(node)) continue;
    // The voice's own bar is not part of the tour.
    if (node.closest("[data-live-voice-bar]")) continue;
    const label = labelOf(node);
    if (!label) continue;
    seen.add(node);
    const role = node.getAttribute("role");
    const kind = role || (node.tagName === "A" ? "link" : node.tagName.toLowerCase());
    out.push({ el: node, label, kind, sensitive: isSensitive(node, label) });
  }
  return out;
}

/** What the voice can point at right now, for the screen_controls tool. */
export function describeScreen(limit = 90): string {
  const items = controls().slice(0, limit);
  if (!items.length) return "No control is visible right now.";
  const heading = document.querySelector<HTMLElement>("h1, h2, [role=heading]")?.innerText?.trim();
  const lines = items.map((item) => `- ${item.label} (${item.kind}${item.sensitive ? ", user only" : ""})`);
  return [heading ? `Screen: ${heading.slice(0, 80)}` : "", "Visible controls:", ...lines].filter(Boolean).join("\n");
}

function find(label: string): Control | null {
  const wanted = normalize(label);
  if (!wanted) return null;
  const items = controls();
  return (
    items.find((item) => normalize(item.label) === wanted)
    ?? items.find((item) => normalize(item.label).startsWith(wanted))
    ?? items.find((item) => normalize(item.label).includes(wanted))
    ?? items.find((item) => wanted.includes(normalize(item.label)) && normalize(item.label).length > 3)
    ?? null
  );
}

let overlay: HTMLDivElement | null = null;
let overlayTimer: number | null = null;

function clearHighlight(): void {
  if (overlayTimer !== null) window.clearTimeout(overlayTimer);
  overlayTimer = null;
  overlay?.remove();
  overlay = null;
}

function highlight(el: HTMLElement): void {
  clearHighlight();
  const rect = el.getBoundingClientRect();
  const pad = 6;
  const ring = document.createElement("div");
  ring.setAttribute("aria-hidden", "true");
  ring.setAttribute("data-live-tour", "");
  Object.assign(ring.style, {
    position: "fixed",
    left: `${rect.left - pad}px`,
    top: `${rect.top - pad}px`,
    width: `${rect.width + pad * 2}px`,
    height: `${rect.height + pad * 2}px`,
    borderRadius: "10px",
    border: "2px solid #1d6af0",
    // The rest of the screen dims a little: the eye goes to the control.
    boxShadow: "0 0 0 4px rgba(29, 106, 240, 0.25), 0 0 0 9999px rgba(0, 0, 0, 0.18)",
    pointerEvents: "none",
    zIndex: "2147483000",
    transition: "opacity 200ms ease",
  });
  ring.animate?.(
    [{ transform: "scale(1.06)", opacity: 0 }, { transform: "scale(1)", opacity: 1 }],
    { duration: 260, easing: "cubic-bezier(0.2, 0.8, 0.2, 1)" },
  );
  document.body.appendChild(ring);
  overlay = ring;
  overlayTimer = window.setTimeout(clearHighlight, HIGHLIGHT_MS);
}

function press(el: HTMLElement): void {
  // Radix menus and tabs open on pointer down, plain buttons on click.
  const init = { bubbles: true, cancelable: true, button: 0, pointerType: "mouse" } as PointerEventInit;
  el.dispatchEvent(new PointerEvent("pointerdown", init));
  el.dispatchEvent(new MouseEvent("mousedown", init));
  el.dispatchEvent(new PointerEvent("pointerup", init));
  el.dispatchEvent(new MouseEvent("mouseup", init));
  el.click();
  if (el.tagName === "INPUT" || el.tagName === "TEXTAREA") el.focus();
}

/** Point at a control, and press it when asked and allowed. Returns what happened, for the voice. */
export async function showControl(label: string, click: boolean): Promise<string> {
  const target = find(label);
  if (!target) {
    return `No visible control matches "${label}". ${describeScreen(40)}`;
  }
  target.el.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" });
  await new Promise((resolve) => window.setTimeout(resolve, 350));
  highlight(target.el);
  if (!click) return `Highlighted "${target.label}" on screen.`;
  if (target.sensitive) {
    return `Highlighted "${target.label}" but did not press it: it is a sensitive action, the user presses it themselves.`;
  }
  await new Promise((resolve) => window.setTimeout(resolve, CLICK_DELAY_MS));
  press(target.el);
  return `Highlighted and pressed "${target.label}".`;
}

export function openPlace(place: string): string {
  if (!(TOUR_PLACES as readonly string[]).includes(place)) {
    return `Unknown place "${place}". Known places: ${TOUR_PLACES.join(", ")}.`;
  }
  clearHighlight();
  window.dispatchEvent(new CustomEvent(TOUR_OPEN_EVENT, { detail: { place } }));
  return `Opened ${place.replace("/", " > ")}. Call screen_controls to see what is on screen.`;
}
