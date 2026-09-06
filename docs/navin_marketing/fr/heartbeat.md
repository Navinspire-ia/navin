# Heartbeat Marketing

Le heartbeat de Studio `#/marketing` est un **watch silencieux**. Ce n'est pas la boucle de croissance.

Le gateway a deja lance `marketing action=watch` avant le tour LLM (`tick_heartbeat_desks`). L'agent peut rappeler watch (idempotent). Il ne doit jamais start, schedule, tick, understand, publier ou depenser.

## Politique (`HEARTBEAT.md`)

Fichier live : `.navin/HEARTBEAT.md`. Modele : `navin/templates/HEARTBEAT.md`. Les deux partagent le bloc **Marketing winners**.

Si `watch.count` est 0 et que le prompt n'a pas de digest Marketing, l'agent repond `HEARTBEAT_OK` et s'arrete.

Si count > 0, il ne rapporte que ce digest. La note ajoutee au prompt dit :

```
Never publish. Never spend ad budget. Never start the growth loop from heartbeat.
```

## Actions autorisees sur un tour heartbeat

`status`, `snapshot`, `watch`.

Double garde :

1. `handle_marketing_action` (HTTP / CLI / desk)
2. `MarketingTool.execute` (outil agent)

Toute autre action renvoie un refus heartbeat.

## Comportement du watch

| Cas | Resultat |
| --- | --- |
| Marque / produit pas arme | `None` (ce desk est saute) |
| Loop a watch il y a moins de 90 s | `skipped=loop_just_watched`, count 0 |
| Cycle live (lock ou PID) | `skipped=busy`, count 0 |
| Watch plus long que 20 s | `skipped=error`, le gateway reste libre |
| Nouveaux gagnants ou fingerprint concurrent | Events + digest |
| Meme gagnant deja alerte | Pas renvoye (`alerts_sent`) |
| Tous les canaux notify fermes | `delivered=false`, `last_watch` non pose, le prochain heartbeat retente |

Les alertes partent vers le centre WebUI et les `settings.channels` optionnels (Telegram, WhatsApp, email). Un canal ferme ne fait jamais echouer le watch.

Les events concurrent partent quand le fingerprint analytics / concurrents change, et seulement pour la derniere ligne pas encore marquee pour ce fingerprint.

## Isolation

Un crash de desk ne saute jamais les autres. Ordre dans `tick_heartbeat_desks` : Tenders → Career → Leads → **Marketing** → Trading.

Le heartbeat ne garde pas le lock plus d'un passage watch (`wait_s=0`).

## Ce qui reste sur le desk

Understand, pipeline, approve, content, creative, metrics, improve, launch, start, stop, schedule, tick.

Voir [Boucle de croissance](./loop.md).
