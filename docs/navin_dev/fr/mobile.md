# Mobile

Lancer et prévisualiser des apps Expo, React Native et Flutter dans l'atelier Dev.

Référence technique complète : [docs/mobile.md](../../mobile.md).

## Où est l'onglet Mobile ?

Atelier Dev (`#/code`) → barre d'onglets au centre, à droite de **Preview** (web) :

**Code** · **Preview** · **Mobile** · Graph · …

Aussi : bouton **Lancer Mobile** dans la barre d'outils, et Actions → Qualité → **Lancer Mobile**.

## Démarrage rapide

1. Ouvrir un projet Expo / React Native / Flutter dans Dev.
2. Dans le chat : `/mobile android` (ou cliquer **Lancer Mobile**).
3. Vérifier qu'un émulateur Android ou un téléphone apparaît dans `adb devices`.
4. Demander un preview live (`mobile(action="preview_start")`) - l'onglet **Mobile** s'ouvre et démarre automatiquement (ou ouvrir l'onglet et cliquer **Démarrer le preview**).
5. Cliquer sur l'écran pour taper ; glisser pour swiper. Boutons Retour / Accueil / Récents dans la barre.

## Commande chat

| Commande | Exemple |
|---|---|
| `/mobile` | `/mobile android`, `/mobile doctor`, `/mobile web` |

Charge le skill `mobile-dev` et pilote l'outil `mobile` (detect → doctor → run → preview).

## Outil agent (résumé)

```text
mobile(action="detect")
mobile(action="doctor")
mobile(action="run", target="android")
mobile(action="preview_start")
mobile(action="tap", x=540, y=1200)
mobile(action="ui_dump")
mobile(action="logs")
mobile(action="stop")
```

## Prérequis

- Node.js (Expo / React Native) ou SDK Flutter
- Android SDK platform-tools (`adb`) pour le preview appareil
- Optionnel : rebuild de l'accélérateur Rust avec `make native` pour une capture plus rapide

## Limites

- iOS nécessite un Mac avec Xcode.
- Le preview est Android (adb screencap). La cible Expo **web** n'utilise pas le panneau appareil.
- Le lien composant → fichier source passe par `ui_dump` (testID / libellés), pas encore un inspecteur complet.
