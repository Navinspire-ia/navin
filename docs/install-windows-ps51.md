# Installeur Windows : « no Windows package » sous PowerShell 5.1

## Symptome

Sur un client Windows fraichement installe :

```powershell
irm 'https://navin.live/install?win32=true' | iex
navin-install: version 2.0.8 2.0.7 2.0.6 ... 1.0.0 (windows/x64)
no Windows package in v2.0.8 2.0.7 ... 1.0.0. See https://navin.live/download
```

La liste complete des versions apparait comme « version », puis l'installeur
echoue alors que les paquets Windows existent.

## Cause

Le fichier `releases.json` heberge sur S3 est un tableau JSON. Sous Windows
PowerShell 5.1, `ConvertFrom-Json` renvoie ce tableau comme un seul objet :
`$data[0]` devient le tableau entier, et `$data[0].version` enumere les membres
- d'ou la chaine « 2.0.8 2.0.7 ... 1.0.0 ». Aucune comparaison de nom de paquet
ne peut alors matcher, d'ou le `throw "no Windows package"`.

Le correctif existe dans le depot depuis le commit 788a5e38 (prod-v2) :
`Get-LatestVersion` deballe le tableau wrappé avant de lire la version
(`windows.ps1`, embarque dans `site/front/src/app/install/scripts.generated.ts`).

## Etat de l'incident (2026-09-25)

| Verification | Resultat |
| --- | --- |
| `releases.json` S3 v2.0.8 | OK : `navin-cli-2.0.8-windows-x64.zip` et `Navin-Desktop-2.0.8-windows-x64-setup.exe` presents |
| Script servi par `navin.live/install?win32=true` | OBSOLETE : pas de deballage PS 5.1 |
| Depot local | Corrige, 23 tests pytest verts dont le test de regression `test_served_windows_version_check_unwraps_before_returning` |

## Reste a faire

Redeployer le site navin.live pour que `/install` serve le
`scripts.generated.ts` regenere. La methode de deploiement du site n'est
documentee nulle part dans le depot (aucun workflow, cible Makefile ou script).

### Verification apres deploiement

1. Telecharger le script servi et verifier qu'il contient le deballage :

```powershell
irm 'https://navin.live/install?win32=true' | Out-String | Select-String 'System.Array'
```

La commande doit afficher la ligne `while ($data.Count -ge 1 -and $data[0] -is [System.Array])`.

2. Relancer l'installeur complet sur le client :

```powershell
irm 'https://navin.live/install?win32=true' | iex
```

Sortie attendue : `navin-install: version 2.0.8 (windows/x64)` puis le
telechargement du paquet.

## Deblocage immediat du client

Le paquet existe deja sur S3, il peut etre installe directement :

```powershell
irm 'https://navinagent.s3.eu-north-1.amazonaws.com/v2.0.8/Navin-Desktop-2.0.8-windows-x64-setup.exe' -OutFile "$env:TEMP\navin-setup.exe"; Start-Process "$env:TEMP\navin-setup.exe"
```
