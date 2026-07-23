# Module Marketing — Vue d'ensemble

Le module **Marketing** (barre latérale → **Marketing**, route `#/marketing`) est un studio marketing complet. À partir d'un simple brief, l'agent produit de vrais livrables de bout en bout : copies publicitaires, posts sociaux natifs par plateforme, visuels produit, vidéos publicitaires avec scripts, séquences email, copy de landing page — chaque asset enregistré comme fichier dans votre espace de travail.

## Fonctionnement

1. Ouvrez **Marketing** dans la barre latérale.
2. (Optionnel) Tapez un **brief** en haut : produit, cible, objectif, ton. Il est joint à chaque action.
3. Choisissez une carte d'action dans l'un des trois groupes — **Créatif**, **Contenu**, **Stratégie** (voir [Actions](./actions.md)).
4. Le chat s'ouvre et `/campaign` part automatiquement avec la spécification de l'action et votre brief.
5. L'agent définit le persona et le message clé, produit les livrables, enregistre les assets, et termine par un tableau récapitulatif des fichiers et chemins.

Usage direct dans n'importe quel chat :

```
/campaign campagne de lancement pour notre gourde écologique, cible sportifs urbains
```

## La commande `/campaign`

| | |
| --- | --- |
| Commande | `/campaign [brief]` |
| Cycle de vie | Workflow agent (tour d'agent complet) |
| Skills préchargés | `campaign-manager`, `ad-creative-generator`, `social-media-manager`, `copywriting-agent`, `image-generation`, `video-generation`, `brand-voice-manager`, `customer-persona-builder` |
| Sortie | Copies, images, vidéos, plans — enregistrés dans l'espace de travail avec un récapitulatif des livrables |

## Providers de génération média

La génération d'images et de vidéos utilise les providers configurés dans **Réglages** :

- **Images** — le provider/modèle de génération d'images configuré produit packshots produit, scènes lifestyle, visuels sociaux et assets de marque. Des variantes par ratio (carré, story, paysage) sont générées par plateforme.
- **Vidéos** — quand un provider de génération vidéo est configuré, l'agent écrit le script et le storyboard, puis génère la vidéo. Sans provider, il livre quand même le script complet, le découpage par scènes, les textes à l'écran et la voix off, prêts pour la production.

L'agent maintient une voix de marque cohérente sur tous les assets (`brand-voice-manager`) et peut étudier le marché et les concurrents avec les outils web avant de créer.

## Ce que vous pouvez produire

| Catégorie | Livrables |
| --- | --- |
| Créatif | Images produit, vidéos pub, visuels sociaux par format, kits de marque (pistes de logo, palette, typographie, voix) |
| Contenu | Posts sociaux par plateforme avec accroches et hashtags, articles de blog, séquences de 5 emails, copy de landing page |
| Stratégie | Campagnes 360°, personas clients, plans de contenu 30 jours, analyses concurrentielles |

## Astuces

- Donnez le brief une fois en haut ; chaque carte le réutilise.
- Enchaînez les actions dans le même chat : le persona d'abord, puis la campagne 360° en hérite.
- Demandez des variantes par plateforme : « adapte le script vidéo pour TikTok (15 s) et YouTube (30 s) ».
- Tout est fichier : images, vidéos, calendriers et copies atterrissent dans l'espace de travail, prêts à publier.
