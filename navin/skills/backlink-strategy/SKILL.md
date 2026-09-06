---
name: backlink-strategy
description: Identify realistic link opportunities - partners, guest posts, directories, digital PR, and linkable assets. Use to build authority for a domain.
metadata: {"navin":{"emoji":"🔗","category":"seo"}}
---

# Backlink Strategy

Links follow value. Plan assets worth linking to, then a realistic outreach list - no link farms, no PBNs.

## When to use

- Authority building for a domain or money page
- After competitor gap analysis shows link disparity
- Launch of a linkable asset (study, tool, data)

## When not to use

- Requests to buy links, PBNs, or automated spam
- Pure on-page fixes with no outreach need

## Opportunity types (by effort)

| Type | Effort | Examples |
|------|--------|----------|
| Existing relationships | Low | clients, partners, suppliers, associations |
| Directories / profiles | Low | industry directories, chambers, local listings |
| Unlinked mentions | Low | brand mentions via search - ask for the link |
| Guest posts | Medium | niche blogs, industry media |
| Digital PR / data | High | original studies, benchmarks, free tools |

## Workflow

1. Inventory mentions: `web_search` `"domain" -site:domain` for existing coverage.
2. Mine competitor sources (`competitor-seo-analysis` / `seo-data-provider` if available).
3. Propose 1-2 linkable assets (stat page, calculator, industry report) with owner + timeline.
4. Build a target list with a contact angle per row (why *they* would link).
5. Draft outreach with `cold-email-writer` tone - personalized, short, value-first.
6. Include internal linking plan for money pages (anchors, mesh between clusters).
7. Save `seo/backlink-plan-<date>.md`.

## Deliverable

```markdown
## Link strategy - <domain>

### Linkable assets
| Asset | Why it earns links | Owner | ETA |

### Targets
| Target site | Type | Contact angle | Asset to pitch | Priority | Source URL |

### Internal mesh
| From | To | Anchor | Why |

### Outreach templates
...
```

## Rules

- Never propose buying links or automated spam.
- Anchor text diversity: mostly brand/natural, few exact-match.
- Track wins in `seo-monitoring`.
- Without backlink API data, qualify targets qualitatively and say so.

## Anti-patterns

- 200-directory spam lists
- Exact-match anchor schemes
- Promising "DR+20 in 30 days"
