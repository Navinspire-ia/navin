// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

const api = globalThis.browser || globalThis.chrome;
if (api.storage.local.setAccessLevel) void api.storage.local.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" });

async function captureTab(tab) {
  const captureId = crypto.randomUUID();
  let capture;
  try {
    if (!tab.id || !/^https?:\/\//.test(tab.url || "")) throw new Error("Ouvrez une page de mission, de profil ou de prospect avant de cliquer sur Navin.");
    const result = await api.scripting.executeScript({ target: { tabId: tab.id }, files: ["capture.js"] });
    capture = result[0]?.result || { error: "La page ne fournit aucun contenu lisible." };
  } catch {
    capture = { error: "La page ne permet pas la capture. Ouvrez le détail d'une fiche ou utilisez la saisie manuelle." };
  }
  // Drafts are confined to trusted extension pages and disappear at browser exit.
  const stored = await api.storage.session.get(null);
  const expired = Object.entries(stored).filter(([key, value]) => key.startsWith("capture:") && (!value.at || value.at < Date.now() - 3600000)).map(([key]) => key);
  if (expired.length) await api.storage.session.remove(expired);
  await api.storage.session.set({ [`capture:${captureId}`]: { ...capture, at: Date.now() } });
  return api.runtime.getURL(`index.html?capture=${captureId}&panel=1`);
}

api.runtime.onMessage.addListener((message, sender, respond) => {
  if (message?.type !== "navin:capture" || sender.id !== api.runtime.id || !sender.tab || sender.frameId !== 0) return false;
  captureTab(sender.tab).then(url => respond({ url }), () => respond({ error: "Impossible de lire cette page. Rechargez-la puis réessayez." }));
  return true;
});

api.action.onClicked.addListener(async tab => {
  try {
    await api.tabs.sendMessage(tab.id, { type: "navin:open" });
  } catch {
    try {
      await api.scripting.executeScript({ target: { tabId: tab.id }, files: ["overlay.js"] });
      await api.tabs.sendMessage(tab.id, { type: "navin:open" });
    } catch {
      await api.tabs.create({ url: await captureTab(tab) });
    }
  }
});
