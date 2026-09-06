# Actions Scraping - 12 cartes

Chaque carte envoie `/scrape` avec une spécification précise ; votre brief est ajouté.

## Collecter

| Action | Livre |
| --- | --- |
| Scraper une page | Texte/markdown propre depuis une ou plusieurs URL via `scrape action=fetch`. JSON + Markdown sous `scrape/`, avec un court résumé qualité. |
| Crawler un site | BFS same-domain via `scrape action=pipeline` avec plafonds profondeur/pages. Export XLSX + rapport HTML ; signale les pages vides/JS pour un passage browser. |
| Depuis sitemap / liste | Fetch parallèle d'une liste d'URL ou de graines sitemap. CSV + JSONL dédupliqués avec compteurs ok/erreur. |
| Page rendue en JS | Utilise l'outil `browser` pour les pages client-side, normalise en enregistrements scrape, puis `scrape action=export`. |

## Nettoyer & enrichir

| Action | Livre |
| --- | --- |
| Nettoyer un corpus | Retire le chrome restant, normalise les espaces, supprime les quasi-doublons ; réécrit les fichiers avec une note de ce qui a été retiré. |
| Enrichir les métadonnées | Ajoute description/OG, langue, word count, `fetched_at` ; JSON/CSV enrichi + dictionnaire de champs. |
| Extraire les tableaux | Tableaux HTML → lignes CSV/Excel structurées avec URL source et colonnes normalisées. |
| Préparer pour le RAG | Un Markdown par page avec frontmatter YAML (`url`, `title`, `fetched_at`) plus `manifest.json`. |

## Exporter

| Action | Livre |
| --- | --- |
| Export Excel | Classeur (`xlsx`) avec url, title, status, text, error ; feuille résumé optionnelle. |
| Export CSV + JSON | Dumps machine avec compteurs alignés et note de schéma. |
| Export XML | XML `pages/page` bien formé pour ingest legacy. |
| Rapport HTML | Rapport lisible (`format=report`) + synthèse : pages, taux de succès, chemins, prochain crawl. |
