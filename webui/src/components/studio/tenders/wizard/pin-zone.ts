export function pinElementToScrollStart(el: HTMLElement | null) {
  if (!el) return;
  const header = el.querySelector<HTMLElement>("[data-zone-header]");
  header?.focus({ preventScroll: true });
  el.scrollIntoView({ block: "start", inline: "nearest", behavior: "auto" });
}
