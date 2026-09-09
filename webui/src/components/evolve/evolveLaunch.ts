// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { CampaignKind, CampaignParams } from "@/lib/evolve-api";

/** Build the daemon params for an Evolve launch from the workbench form.
 *
 * A proof of pending chat/file edits must send `dirty: true`. Without it the
 * engine shadows HEAD and the agent's uncommitted writes never reach the bench.
 */
export function evolveLaunchParams(
  kind: CampaignKind,
  fields: {
    start?: string;
    url?: string;
    test?: string;
    objective?: string;
    profile?: string;
    preset?: string;
    includeDirty?: boolean;
  },
): CampaignParams {
  const params: CampaignParams = {
    start: fields.start || undefined,
    url: fields.url || undefined,
    test: fields.test || undefined,
  };
  if (kind === "optimize.run") {
    params.objective = fields.objective;
    params.duration = 8;
    params.max_variants = 3;
  } else {
    params.profile = fields.profile;
  }
  if (kind !== "proof.run" && fields.preset) {
    params.preset = fields.preset;
  }
  if (kind === "proof.run") {
    params.dirty = fields.includeDirty !== false;
  }
  return params;
}
