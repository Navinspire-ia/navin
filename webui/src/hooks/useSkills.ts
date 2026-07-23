import { useCallback, useEffect, useState } from "react";

import { fetchSkills } from "@/lib/api";
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

  return { skills, refresh };
}
