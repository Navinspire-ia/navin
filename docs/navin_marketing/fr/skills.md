# Skills marketing

## Precharges sur le module Marketing

Ces skills se chargent avec `product_module=marketing` (Studio `#/marketing`) meme sans commande slash.

| Skill | Role |
| --- | --- |
| `marketing-strategist` | Cable a l'outil `marketing` : status, brand, understand, start/stop/schedule. La source de verite est le store du desk. |
| `growth-marketing` | Experiences sur le desk (`improve` / metrics). Revue hebdo = boucle desk, jamais une cron de chat. |
| `digital-marketing` | Funnel et mix canaux. Barre super render pages vs PPT. |
| `email-marketing` | Sequences et delivrabilite. Persister la copy avec `marketing action=content`. |
| `marketing-analytics` | Honnetete de mesure. Ecrire les chiffres avec `marketing action=metrics`. |

## Precharges par `/campaign` / `/montage`

| Skill | Role |
| --- | --- |
| `studio-expert-contract` | Contrat senior : preuves, livrables, PASS/WARN/BLOCK. Aussi sur `/marketing`. |
| `critic-reviewer` | Revue critique avant livraison. |
| `campaign-manager` | Brief, message house, calendrier, UTMs (`check_campaign_brief.py`). Persister avec `plan`. |
| `ui-ux-pro-max` | Stack page : Motion, Lenis, Embla, Lucide, three + R3F + drei. 3D dessinee. |
| `presentation-designer` | Decks : type affiche, photos, charts natifs. Pas de WebGL sur un slide. |
| `pptx-generator` | PPTX editable. Un slide screenshot est refuse. |
| `ad-creative-generator` | Concepts et placements. Puis `generate_image` / `generate_video` + QA visuel. |
| `social-media-manager` | Formats natifs et cadence. |
| `copywriting-agent` | Titres, corps, CTA. |
| `image-generation` / `video-generation` | Providers configures. |
| `product-visuals` | Packshots, lifestyle, 360. Pas de faux 3D dans PowerPoint. |
| `brand-voice-manager` | Voix coherente. |
| `customer-persona-builder` | Personas documentes. |
| `montage-studio` | Demo live, exports sous `marketing/montage/`. Jamais d'auto-publish. |

## Complementaires

| Skill | Role |
| --- | --- |
| `market-research` / `competitor-intelligence` | Puis `marketing action=competitor` ou `research`. |
| `paid-ads-manager` | Preferer le studio **Ads** `/ads` + presets MCP. |
| `go-to-market-planner` | Plans de lancement. Apparier avec `marketing action=launch`. |
| `conversion-rate-optimization` | Landing et funnel. |
| `content-generation` / `content-recycler` / `blog-writer` | Echelle et recyclage. |
| `fact-checker` / `proofreader` | Passe qualite. Toujours pas d'auto-publish. |

Invoquez un skill par nom pour une passe ciblee. Les faits que la boucle doit voir passent encore par l'outil `marketing`.
