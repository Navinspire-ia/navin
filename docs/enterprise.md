# Navin Enterprise

Enterprise sits above Team: quote-based pricing (no self-serve checkout), high execution limits, SSO configuration storage, append-only audit, and org RBAC.

Individual Free remains available with bring-your-own keys (BYOK) in the desktop app under **Settings → Providers**.

## Plan

- Enterprise is contact / sales led. Pricing UI shows **Contact** / Contact sales rather than a Stripe checkout.
- Limits cover high concurrent agents, devices, and steps.
- AI budget is contract-specific.

Assign Enterprise from the site admin console or sales ops after a signed quote. Prefer sales-led onboarding; do not expect a self-serve purchase path.

## What Enterprise adds for orgs

| Capability | In product terms |
| --- | --- |
| Audit | Append-only audit events org admins can read |
| SSO config | Per-org OIDC/SAML settings storage (wiring may be staged) |
| RBAC | Roles such as admin, member, viewer for org actions |
| Tool policy | Optional org allow/deny lists for agent tools |

## SSO (staged)

SSO configuration can be stored for an org. Full end-to-end login wiring depends on your IdP path (for example WorkOS or Supabase SAML). Work with Navin sales / admin to enable SSO for Enterprise orgs.

## Using Enterprise with the desktop app

1. Sign in under **Settings → Account** with your org-linked identity when ready.
2. Keep using **Settings → Providers** / **Models** for BYOK or contracted credits as your plan allows.
3. Respect org tool policy if your admin enables allow/deny lists.
4. Admins review audit and SSO from the site admin surfaces, not from everyday chat.

## Admin note

When assigning Enterprise:

- Prefer after a signed quote (custom AI budget / seats).
- Enterprise is not self-serve Stripe; use assign or sales ops.
- Enable SSO and audit only for orgs on the Enterprise plan.

## Related

- [`team-plans.md`](./team-plans.md)
- [`org-projects.md`](./org-projects.md)
- [`start-without-technical-background.md`](./start-without-technical-background.md)
