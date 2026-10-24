// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Settings > Sites & logins: the URL and login of an app (yours or an
 * external one) so agents can sign in and record demo videos. Values go to
 * the gateway vault and never come back; the agent only ever sees refs such
 * as site:app.example.com/password, so a password never enters the chat.
 *
 * The UI lives in SiteLoginsPanel so the Dev, Marketing and Montage desks can
 * embed the same list and form inline.
 */

import { SiteLoginsPanel } from "@/components/settings/SiteLoginsPanel";

export function SitesSettings({ token }: { token: string }) {
  return <SiteLoginsPanel token={token} />;
}
