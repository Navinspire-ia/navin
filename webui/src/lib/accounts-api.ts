import { apiBodyHeaders, apiRequest } from "./api";

export type AccountPermission = "read_mail" | "draft_mail" | "send_mail" | "archive_mail" | "delete_mail" | "read_calendar" | "write_calendar" | "cancel_events" | "read_contacts";
export type AccountPermissions = Record<AccountPermission, boolean>;
export interface ConnectedAccount { id: string; provider: string; email: string; name: string; permissions: AccountPermissions; capabilities: AccountPermissions }
export interface AccountsSnapshot {
  accounts: ConnectedAccount[]; providers: { id: string; name: string; ready: boolean }[]; vault_ready: boolean;
  pending: { id: string; account_id: string; action: string; body: Record<string, unknown> }[];
}
export const DEFAULT_ACCOUNT_PERMISSIONS: AccountPermissions = { read_mail: true, draft_mail: true, send_mail: false, archive_mail: false, delete_mail: false,
  read_calendar: true, write_calendar: false, cancel_events: false, read_contacts: false };
export function accountAction<T = AccountsSnapshot>(token: string, action: string, body: Record<string, unknown> = {}, signal?: AbortSignal): Promise<T> {
  return apiRequest<T>(`/api/accounts?action=${encodeURIComponent(action)}`, token, { headers: apiBodyHeaders(JSON.stringify(body)), signal });
}
