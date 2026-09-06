---
name: seo-data-provider
description: >
  Pull real SEO metrics (search volume, keyword difficulty, SERP, backlinks)
  from DataForSEO or Semrush when API credentials are present. Use before
  inventing volumes; fall back to qualitative methods when keys are missing.
metadata: {"navin":{"emoji":"📊","category":"seo"}}
---

# SEO Data Provider

Use the `seo` engine's `serp_snapshot` and `serp_history` actions first. They enforce source and confidence on every result and write atomic append-only history. Never invent volume/KD/backlink counts or positions. If no keys are present, preserve the returned `data_gap` and continue with qualitative research.

## When to use

- Keyword maps that need monthly volume or KD
- Competitor backlink / overlap snapshots
- SERP feature checks at scale

## When not to use

- Keys missing (do not fake numbers)
- Pure on-page copy edits

## Credentials (never print secrets)

| Provider | Env | Notes |
|----------|-----|-------|
| DataForSEO | `DATAFORSEO_LOGIN`, `DATAFORSEO_PASSWORD` | Basic auth to `api.dataforseo.com` |
| Semrush | `SEMRUSH_API_KEY` | Query param `key=` |

Prefer DataForSEO when both exist. Prefer Semrush if only that key exists.
Check presence with `printenv DATAFORSEO_LOGIN SEMRUSH_API_KEY` - report only which are set, never values.

## DataForSEO examples

```bash
curl -s -u "$DATAFORSEO_LOGIN:$DATAFORSEO_PASSWORD" \
  "https://api.dataforseo.com/v3/keywords_data/google_ads/search_volume/live" \
  -H "Content-Type: application/json" \
  -d '[{"keywords":["crm logiciel","outil crm"],"location_code":2250,"language_code":"fr"}]'

curl -s -u "$DATAFORSEO_LOGIN:$DATAFORSEO_PASSWORD" \
  "https://api.dataforseo.com/v3/serp/google/organic/live/advanced" \
  -H "Content-Type: application/json" \
  -d '[{"keyword":"crm logiciel","location_code":2250,"language_code":"fr","depth":10}]'
```

Parse with `jq`/`python`; save raw JSON under `seo/api/` and a clean table for the report. Cite provider + date in the keyword map columns.

## Semrush examples

```bash
curl -s "https://api.semrush.com/?type=phrase_these&key=$SEMRUSH_API_KEY&phrase=crm%20logiciel&database=fr&export_columns=Ph,Nq,Kd"
curl -s "https://api.semrush.com/?type=backlinks_overview&key=$SEMRUSH_API_KEY&target=example.com&target_type=root_domain"
```

## Workflow

1. Call `seo` action `serp_snapshot` with `provider=dataforseo` or `provider=semrush`.
2. If credentials are absent, preserve `status=data_gap`; do not add numeric results.
3. Read trends through `seo` action `serp_history`; the store is append-only.
4. Use direct API calls only for unsupported metrics, then retain source, date, and confidence.
5. Never log full API keys; redact in reports.

## Rules

- One source of truth per metric cell (provider name in Notes).
- Respect API rate limits; batch keywords.
- On HTTP/auth errors: report failure, fall back qualitative - do not invent.

## Anti-patterns

- Pasting API keys into markdown reports
- Mixing Semrush KD with invented volumes
