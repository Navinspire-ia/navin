// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useState } from "react";

import { fetchSkills } from "@/lib/api";
import { SKILLS_CHANGED_EVENT } from "@/lib/skill-events";
import type { SkillSummary } from "@/lib/types";

export function useSkills(token: string): {
  skills: SkillSummary[];
  refresh: () => Promise<void>;
} {
  const [skills, setSkills] = useState<SkillSummary[]>([]);

  const refresh = useCallback(async () => {
    try {
      const { skills: nextSkills } = await fetchSkills(token);
      setSkills(nextSkills);
    } catch {
      setSkills([]);
    }
  }, [token]);

  useEffect(() => {
    let cancelled = false;
    fetchSkills(token)
      .then(({ skills: nextSkills }) => !cancelled && setSkills(nextSkills))
      .catch(() => !cancelled && setSkills([]));
    return () => {
      cancelled = true;
    };
  }, [token]);

  useEffect(() => {
    const onChanged = () => {
      void refresh();
    };
    window.addEventListener(SKILLS_CHANGED_EVENT, onChanged);
    return () => window.removeEventListener(SKILLS_CHANGED_EVENT, onChanged);
  }, [refresh]);

  return { skills, refresh };
}
