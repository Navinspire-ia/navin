// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/** Mask credentials before a log line is displayed or persisted locally. */
const RULES: Array<[RegExp, string]> = [
  [/-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g, "[secret masque]"],
  [/\b(Bearer\s+)[A-Za-z0-9._~+/=-]{12,}/gi, "$1[masque]"],
  [/\b(sk|pk|ghp|gho|xox[abp]|AKIA)[-_A-Za-z0-9]{16,}/g, "[masque]"],
  [/([?&](?:token|key|api_key|password|secret)=)[^&\s]+/gi, "$1[masque]"],
  [/\b((?:[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY|PASSWD))\s*[=:]\s*)(\S+)/g, "$1[masque]"],
];

export function redactSecrets(text: string): string {
  let out = text;
  for (const [re, repl] of RULES) out = out.replace(re, repl);
  return out;
}
