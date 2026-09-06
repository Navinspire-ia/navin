# Team plans and organizations

Navin Team is a multi-seat subscription: **$40 per collaborator / month**, with
**$30 OpenRouter AI budget per seat** pooled on the organization.

Examples: Team 5 = $200 / month and $150 AI budget; Team 10 = $400 / month and
$300 AI budget. Seats range from 2 to 50.

Individual plans (Free / Plus / Pro / Ultra) are unchanged. Free remains BYOK
(bring your own keys); Navin does not grant managed trial credits on Free.

## Database

| Migration | Role |
|---|---|
| `site/supabase/2026-08-02-organizations.sql` | `organizations`, `org_members`, `org_invites`, org usage pool |
| `site/supabase/2026-08-02-org-projects.sql` | Shared projects, specialties, ownership zones, events |
| `site/supabase/2026-08-02-synced-threads.sql` | Optional encrypted thread sync stub |
| `site/supabase/2026-08-02-enterprise.sql` | Audit + SSO config (above Team) |

Run **organizations** before enterprise, synced-threads, or org-projects.

Manage seats, invites and shared projects from the account **Team** page (`/account/team`).

## Environment

| Variable | Role |
|---|---|
| `STRIPE_PRICE_TEAM` | Stripe price id for $40/seat recurring |
| `UPSTASH_REDIS_REST_URL` / `TOKEN` | Optional shared rate-limit (else in-memory) |

See `site/.env.example`.

## Related

- Shared projects and ownership: [Organization projects](./org-projects.md)
- Enterprise (SSO, audit, evals): [Enterprise](./enterprise.md)
- Hybrid presence / ACL: collab tokens in `navin/webui/collab_tokens.py`, ACL in `navin/collab/acl.py`
