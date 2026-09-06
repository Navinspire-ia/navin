import { useEffect, useState } from "react";

import { useAccount } from "@/hooks/useAccount";
import { useClient } from "@/providers/ClientProvider";

/**
 * Effective org role for ACL UI (viewer banner, etc.).
 * Prefers the persisted account payload, falls back to the WS ``ready`` frame.
 */
export function useOrgRole(): string | null {
  const { account } = useAccount();
  const { client } = useClient();
  const [socketRole, setSocketRole] = useState<string | null>(null);

  useEffect(() => {
    setSocketRole(client.getOrgRole());
    return client.onStatus(() => {
      setSocketRole(client.getOrgRole());
    });
  }, [client]);

  return account?.org_role ?? socketRole;
}
