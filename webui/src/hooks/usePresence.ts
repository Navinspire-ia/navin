import { useEffect, useState } from "react";

import { useClient } from "@/providers/ClientProvider";
import type { PresenceMember } from "@/lib/types";

/**
 * Live presence roster for the active chat, driven by gateway WS events.
 */
export function usePresence(chatId: string | null): PresenceMember[] {
  const { client } = useClient();
  const [members, setMembers] = useState<PresenceMember[]>([]);

  useEffect(() => {
    if (!client || !chatId) {
      setMembers([]);
      return;
    }
    return client.onPresence(chatId, setMembers);
  }, [client, chatId]);

  return members;
}
