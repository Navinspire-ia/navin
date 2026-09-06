# Publier l'aperçu (Cloudflare Quick Tunnel)

Dans le module **Dev**, l'onglet **Aperçu** peut exposer le serveur de développement local sur Internet via un [Cloudflare Quick Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/) (`*.trycloudflare.com`). Utile pour une démo, un webhook ou un test mobile - ce n'est pas un hébergement de production.

Aucun compte Cloudflare n'est requis. Navin lance `cloudflared` (binaire sur le PATH, ou téléchargé une fois dans le dossier data de l'instance).

## Ouverture automatique

Quand l'agent crée ou démarre une app web, il appelle `open_preview` une fois
le serveur local prêt (`curl`). L'iframe **Aperçu** s'ouvre avec cette URL -
sans clic **Ouvrir**. Pour Android, `mobile(action="preview_start")` ouvre
l'onglet **Mobile** de la même façon. **Publier** (tunnel public) reste manuel.

Sur un petit écran (téléphone), le chat Dev devient une feuille en bas pour
laisser l'aperçu en pleine largeur ; l'ouverture auto referme aussi le chat
pour garder l'app au premier plan.

## Utilisation

1. Lancez un serveur de dev dans le projet (`npm run dev`, etc.) - ou laissez l'agent le faire.
2. Ouvrez **Aperçu** (ouvert automatiquement si l'agent appelle `open_preview`) et vérifiez l'URL locale (ex. `http://127.0.0.1:5173`).
3. Cliquez **Publier** à côté de **Ouvrir**.
4. Navin lit le **port** de cette URL (il change souvent à chaque démarrage Vite/Next) et lance :

   ```bash
   cloudflared tunnel --url http://127.0.0.1:<port> \
     --http-host-header 127.0.0.1:<port> --no-autoupdate
   ```

5. L'URL publique (`https://….trycloudflare.com`) s'affiche : copier, ouvrir, ou **Arrêter**.

Le tunnel réécrit l'en-tête `Host` vers `127.0.0.1:<port>`, donc Vite / Next / etc. n'ont **pas** besoin d'ajouter `allowedHosts` dans leur config.

Prérequis : connexion Internet. La publication n'est disponible qu'en **localhost** (même machine que le gateway). Au premier usage, le binaire `cloudflared` peut être téléchargé automatiquement.

## Ce que ça n'est pas

| Attente | Réalité |
| --- | --- |
| Hébergement prod permanent | Non - tunnel temporaire vers votre machine |
| Domaine custom `monsite.com` | Non - URL aléatoire `*.trycloudflare.com` |
| Compte Cloudflare obligatoire | Non |

## Compte Cloudflare ?

| Objectif | Compte Cloudflare ? |
| --- | --- |
| Partage rapide via **Publier** (Quick Tunnel) | Non |
| Domaine / sous-domaine **à vous** | Oui - tunnel nommé Cloudflare, hors périmètre actuel |

Le bouton **Publier** dans l'aperçu démarre et arrête le tunnel. Il n'y a rien à configurer à la main. Le tunnel est coupé à l'arrêt du gateway.

## Dépannage

| Message | Cause probable | Piste |
| --- | --- | --- |
| `This host is not allowed` (Vite) | Ancien tunnel sans rewrite Host | Arrêter, republier (Navin envoie `Host: 127.0.0.1`) |
| `could not download cloudflared` | Réseau / GitHub inaccessible | Vérifier Internet, ou installer `cloudflared` sur le PATH |
| `Nothing is listening on that local port` | Serveur de dev arrêté | Relancer le serveur, republier |
| `Enter a local URL with a port` | URL sans port explicite | Ex. `http://127.0.0.1:5173` |
| `preview tunnel is localhost-only` | Accès distant au gateway | Publier depuis la machine qui exécute Navin |

## Voir aussi

- [Atelier](./workbench.md) - panneau Aperçu / Navigateur
- [Cloudflare TryCloudflare](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/)
