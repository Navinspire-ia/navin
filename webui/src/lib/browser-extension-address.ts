// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

export function extensionServerAddress(raw: string): string {
  const url = new URL(raw.trim());
  if (url.username || url.password || url.search || url.hash || !["", "/"].includes(url.pathname)) {
    throw new Error("Utilisez l'adresse de Navin sans chemin, identifiant ni paramètre.");
  }
  if (["tauri.localhost", "asset.localhost", "0.0.0.0", "[::]"].includes(url.hostname)) {
    throw new Error("Cette adresse interne n'est pas accessible depuis l'extension.");
  }
  if (url.protocol !== "https:" && !(url.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname))) {
    throw new Error("Utilisez HTTPS pour Navin en ligne, ou HTTP sur la boucle locale pour Navin sur cet ordinateur.");
  }
  return url.origin;
}

export function extensionPairingAddress(pageUrl: string): string {
  const url = new URL(pageUrl);
  if (url.username || url.password) throw new Error("L'adresse contient des identifiants.");
  // Hash routes may contain bootstrap/session credentials: never copy them.
  return extensionServerAddress(url.origin);
}

export function parseExtensionPairing(raw: string): { address: string; code: string } {
  const value = JSON.parse(raw) as { navin_url?: unknown; code?: unknown; type?: unknown };
  if (value?.type !== "navin-browser-pairing" || typeof value.navin_url !== "string" || typeof value.code !== "string"
    || !/^[A-Za-z0-9 -]{6,80}$/.test(value.code)) throw new Error("Les informations d'appairage sont invalides.");
  return { address: extensionServerAddress(value.navin_url), code: value.code };
}
