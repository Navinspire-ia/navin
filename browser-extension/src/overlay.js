// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only
(() => {
  if (globalThis.__navinOverlay || window.top !== window) return;
  globalThis.__navinOverlay = true;
  const api = globalThis.browser || globalThis.chrome;
  const host = document.createElement("div");
  host.setAttribute("data-navin-overlay", "");
  host.style.cssText = "all:initial!important;position:fixed!important;inset:0!important;z-index:2147483647!important;pointer-events:none!important;";
  const shadow = host.attachShadow({ mode: "closed" });
  const style = document.createElement("style");
  style.textContent = `
    * { box-sizing:border-box; font-family:'Segoe UI',system-ui,sans-serif; }
    button { cursor:pointer; min-height:44px; border:0; font-size:14px; }
    button:focus-visible { outline:3px solid #68b5ff; outline-offset:3px; }
    button:active { transform:scale(.96); }
    .bubble { position:fixed; right:18px; bottom:96px; border-radius:28px; padding:0 18px;
      height:52px; color:white; background:#155bba; box-shadow:0 4px 20px #10204040; pointer-events:auto; }
    .panel { position:fixed; right:12px; top:12px; bottom:12px; width:min(480px,calc(100vw - 24px));
      background:#f6f8fb; border-radius:16px; box-shadow:0 8px 40px #10204040; overflow:hidden; pointer-events:auto; }
    .bar { display:flex; align-items:center; gap:8px; padding:8px 12px; background:white; color:#15263c; }
    .bar strong { flex:1; } .bar button { background:#eaf2fc; color:#155bba; border-radius:8px; padding:0 12px; }
    iframe { display:block; width:100%; height:calc(100% - 60px); border:0; }
    .status { padding:20px; color:#15263c; } [hidden] { display:none!important; }
  `;
  const bubble = document.createElement("button");
  bubble.className = "bubble";
  bubble.textContent = "Navin";
  bubble.setAttribute("aria-label", "Ouvrir le panneau Navin");
  bubble.setAttribute("aria-expanded", "false");
  const panel = document.createElement("section");
  panel.className = "panel";
  panel.setAttribute("aria-label", "Navin : analyser cette page");
  panel.hidden = true;
  const bar = document.createElement("div");
  bar.className = "bar";
  const title = document.createElement("strong");
  title.textContent = "Navin";
  const refresh = document.createElement("button");
  refresh.textContent = "Relire la page";
  refresh.title = "Remplacer la capture par la page actuelle";
  const close = document.createElement("button");
  close.textContent = "Fermer";
  const status = document.createElement("p");
  status.className = "status";
  status.setAttribute("role", "status");
  const frame = document.createElement("iframe");
  frame.title = "Analyse et import Navin";
  frame.hidden = true;
  bar.append(title, refresh, close);
  panel.append(bar, status, frame);
  shadow.append(style, bubble, panel);
  document.documentElement.append(host);
  let capturedUrl = "";
  const capture = async () => {
    refresh.disabled = true;
    status.hidden = false;
    status.textContent = "Lecture de la page en cours...";
    try {
      const response = await api.runtime.sendMessage({ type: "navin:capture" });
      if (!response?.url) throw new Error(response?.error || "Rechargez la page après la mise à jour de l'extension.");
      frame.src = response.url;
      frame.hidden = false;
      status.hidden = true;
      capturedUrl = location.href;
    } catch (error) {
      status.textContent = error.message;
    } finally { refresh.disabled = false; }
  };
  const open = () => {
    panel.hidden = false;
    bubble.hidden = true;
    bubble.setAttribute("aria-expanded", "true");
    if (!frame.src || capturedUrl !== location.href) void capture();
    close.focus({ preventScroll: true });
  };
  const hide = () => {
    panel.hidden = true;
    bubble.hidden = false;
    bubble.setAttribute("aria-expanded", "false");
    bubble.focus({ preventScroll: true });
  };
  // Keep the page's selection intact for LinkedIn's selection-only capture.
  bubble.addEventListener("mousedown", event => event.preventDefault());
  refresh.addEventListener("mousedown", event => event.preventDefault());
  bubble.addEventListener("click", open);
  close.addEventListener("click", hide);
  refresh.addEventListener("click", () => {
    if (confirm("Relire la page et remplacer les modifications non enregistrées dans le panneau ?")) void capture();
  });
  shadow.addEventListener("keydown", event => { if (event.key === "Escape") hide(); });
  api.runtime.onMessage.addListener(message => { if (message?.type === "navin:open") open(); });
})();
