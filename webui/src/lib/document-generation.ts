// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

export interface DocumentGeneration {
  mode: "model" | "deterministic";
  status: "complete" | "needs_review";
  warnings?: string[];
  skills?: {
    module?: string;
    action?: string;
    loaded?: string[];
    unavailable?: { name: string; status: string; reason: string }[];
  };
}
