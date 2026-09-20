<div align="center">

<img src="./assets/readme/hero.svg" alt="Navin: deine KI, dein Rechner. Entwickeln, recherchieren und automatisieren." width="100%">

# Aus einem Ziel wird erledigte Arbeit.

**Ein Open-Source-Arbeitsbereich für KI-Agenten, die programmieren, recherchieren, Werkzeuge nutzen und Kontext zwischen Sitzungen bewahren.**

Im Terminal, Browser oder in der Desktop-App. Wähle deine Modelle. Behalte die Kontrolle.

[English](./README.md) · [Français](./README.fr.md) · [العربية](./README.ar.md) · [Español](./README.es.md) · [Português](./README.pt-BR.md) · [Deutsch](./README.de.md) · [简体中文](./README.zh-CN.md)

[![Lizenz AGPL-3.0](https://img.shields.io/badge/Lizenz-AGPL--3.0-66d9b0?style=flat-square)](./LICENSE) [![Lokale Ausführung](https://img.shields.io/badge/local-first-5599ff?style=flat-square)](./docs/configuration.md) [![GitHub stars](https://img.shields.io/github/stars/Navinspire-ia/navin?style=flat-square&color=ffd166)](https://github.com/Navinspire-ia/navin/stargazers)

**[Loslegen](#loslegen)** · **[Desktop herunterladen](https://navin.live/download)** · **[Dokumentation](./docs/README.md)** · **[Mitmachen](./CONTRIBUTING.md)**

</div>

## Gib Navin einen Auftrag

> „Finde die Ursache dieses Fehlers, behebe ihn, führe die relevanten Tests aus und erkläre die Änderungen.“

> „Recherchiere diese Wettbewerber, vergleiche ihre Angebote mit Quellen und erstelle einen Bericht.“

> „Beobachte dieses Projekt und melde dich, wenn eine Änderung meine Aufmerksamkeit braucht.“

Navin verbindet das Modell mit deinen Dateien, dem Terminal, dem Browser und deinen Werkzeugen. Es kann planen, klar umrissene Aufgaben an Unteragenten delegieren, Ergebnisse prüfen und innerhalb der konfigurierten Grenzen weiterarbeiten.

**Dabei bleibt etwas für die nächste Sitzung erhalten:** Projektwissen, Entscheidungen, wiederverwendbare Fähigkeiten und Arbeitskontext.

## Loslegen

**Linux / macOS / WSL**

```bash
curl https://navin.live/install -fsS | bash
```

**Windows PowerShell**

```powershell
irm 'https://navin.live/install?win32=true' | iex
```

Öffne ein Terminal in deinem Projekt:

```bash
cd your-project
navin-cli
```

1. Öffne **Settings** mit **Ctrl+G** und hinterlege deinen API-Schlüssel oder die Adresse eines lokalen Modells.
2. Wähle Modell und Modus: **Ask**, **Plan**, **Agent**, **Review**, **Security** oder **Debug**.
3. Probiere: **„Lies dieses Projekt und erkläre, wie es funktioniert. Schlage dann eine sinnvolle Verbesserung vor.“**

Lieber mit Fenster? [Navin Desktop für Windows, macOS oder Linux herunterladen](https://navin.live/download).

Die Software ist unter ihrer Open-Source-Lizenz kostenlos nutzbar. Modell-APIs und externe Dienste können Nutzungsgebühren erheben. [Installation und Fehlerbehebung](./docs/Installation.md).

<details>
<summary><strong>Aus dem Quellcode installieren</strong></summary>

```bash
git clone https://github.com/Navinspire-ia/navin.git
cd navin
make install
make start
```

Die Entwicklungs-WebUI läuft unter [localhost:5173](http://localhost:5173). CLI aus dem Repository: `.venv/bin/navin-cli`; unter Windows: `.venv\Scripts\navin-cli`. Voraussetzungen und Produktionsbuilds: [Installationsanleitung](./docs/Installation.md).

</details>

## Ein Arbeitsbereich für viele Aufgaben

| Dein Ziel | Was Navin bietet |
| --- | --- |
| **Software entwickeln** | Repository erkunden, Code bearbeiten, Terminal, Git, Browser-Vorschau, Tests und Review. |
| **Eine Codebasis verstehen** | Projektgraph, Code-Index, Definitionen, Referenzen und Auswirkungsanalyse. |
| **Recherchieren und Daten gewinnen** | Websuche, Browser-Werkzeuge, Scraping, strukturierte Extraktion und Berichte mit Quellen. |
| **Ergebnisse erstellen** | Dokumente, Präsentationen, Tabellen, Architekturdiagramme und Medien-Workflows. |
| **Ein Geschäft voranbringen** | Lead-Recherche, Datenanreicherung, SEO, Marketingrecherche und Kampagnenvorbereitung. |
| **Fachaufgaben bearbeiten** | Ausschreibungsanalyse, Stellensuche, Meeting-Transkription, Notizen und nächste Schritte. |
| **Größere Aufgaben aufteilen** | Unteragenten, die parallel arbeiten und ihre Ergebnisse an den Hauptagenten zurückgeben. |
| **Längerfristig dranbleiben** | Zielorientierte Schleifen, geplante Aufgaben und Heartbeat-Prüfungen bei laufendem Gateway. |

Einige Workflows benötigen zusätzliche Abhängigkeiten, eingerichtete Integrationen oder ein kompatibles Modell. Details stehen in der [Funktionsübersicht](./docs/capabilities.md).

<p align="center">
<img src="./assets/readme/cli.png" alt="Navin CLI mit einem Beispiel zur Erkundung eines Projekts" width="100%">
<br><sub>CLI-Vorschau mit einem Beispiel zur Projekterkundung.</sub>
</p>

## Warum sich ein Blick auf Navin lohnt

**Kontext über das Gespräch hinaus.** Projektgedächtnis und Codegraph helfen, Entscheidungen wiederzufinden und relevante Dateien zu lokalisieren. Das optionale episodische Gedächtnis ergänzt den Rückgriff auf frühere Arbeit. [Gedächtnis](./docs/memory.md) · [Projektgraph](./docs/navin_dev/en/graph.md)

**Werkzeuge für die Umsetzung.** Dateien, Shell, Git, Browser, APIs und MCP verbinden Überlegungen mit Ausführung. Skills bündeln wiederverwendbare Abläufe; Plugins und Integrationen erweitern die Werkzeuge. [MCP einrichten](./docs/guides/configure-mcp-tools.md) · [Skills](./navin/skills/README.md)

**Freie Modellwahl.** Nutze OpenAI, Anthropic, Google, Mistral, DeepSeek, Qwen und weitere Anbieter oder lokale Modelle über Ollama, LM Studio und vLLM. Ordne verschiedenen Aufgaben verschiedene Modelle zu. Die verfügbaren Fähigkeiten hängen von Anbieter und Modell ab. [Konfiguration](./docs/configuration.md)

**Autonomie mit klaren Grenzen.** Schleifen verfolgen ein Ziel, Heartbeat sucht nach sinnvollen Folgeaufgaben. Freigaben, Ressourcenbudgets, Checkpoints und Stoppmöglichkeiten begrenzen die Ausführung. [Automatisierung](./docs/automations.md) · [Sicherheit](./SECURITY.md)

Bei lokaler Ausführung bleibt dein Arbeitsbereich auf deinem Rechner. Wählst du ein entferntes Modell oder einen angebundenen Dienst, werden die für die Anfrage benötigten Daten dorthin gesendet.

## Vom Ziel zum geprüften Ergebnis

<p align="center">
<img src="./assets/readme/agent-workflow.svg" alt="Ziel, Planung, Berechtigungen, Werkzeuge, Prüfung und Lieferung mit Rückfluss ins Projektgedächtnis" width="100%">
</p>

[Interaktives Diagramm herunterladen](./assets/readme/agent-workflow.html) und die HTML-Datei im Browser öffnen. Diagramm und Bedienelemente sind auf Englisch.

## Ein Agent, der aus Erfahrung lernen kann

Navin enthält experimentelle Lernfunktionen zur freiwilligen Aktivierung:

| Ebene | Was sie untersucht |
| --- | --- |
| **Self-Evolve / Auto-Skills** | Wiederholte Fehler in neue Skill-Kandidaten umwandeln, diese bewerten, übernehmen oder zurücknehmen. |
| **World model** | Lokale Vorhersagen über Werkzeugergebnisse aus aufgezeichneten Abläufen lernen. |
| **Policy learning** | Vorschläge für die nächste Aktion anhand zurückgehaltener Evaluationsfälle bewerten. |

Diese Funktionen sind **standardmäßig deaktiviert**. Prüfungen begrenzen die Aktivierung; einen Skill für alle Projekte zu veröffentlichen erfordert eine menschliche Aktion. Das Gesprächsmodell wird dabei nicht nachtrainiert.

Das Ziel sind zunehmend leistungsfähige autonome Agenten. **Navin beansprucht nicht, AGI zu sein.**

[Skill-Entwicklung](./docs/skills-evolution.md) · [World model](./docs/world-model.md) · [Policy learning](./docs/policy.md)

## Verbinde deine Werkzeuge

- **Modelle:** eigene API-Schlüssel oder lokale Inferenz.
- **Erweiterungen:** MCP-Server, Skills, Plugins und eigene Werkzeuge.
- **Kanäle:** Telegram, Slack, Discord, WhatsApp, E-Mail, Teams und weitere über konfigurierte Integrationen.
- **Oberflächen:** CLI, WebUI und herunterladbare Desktop-Apps.

Dieses Repository enthält die öffentliche Agent-Laufzeit, CLI, WebUI und zugehörige Werkzeuge. Desktop-Paketierung, navin.live, Website-Infrastruktur und Release-Veröffentlichung werden separat gepflegt.

## Entwickle Navin mit uns weiter

Probiere eine echte Aufgabe aus. Berichte, wo der Agent geholfen hat und wo er hängen geblieben ist.

- [Melde einen reproduzierbaren Fehler oder schlage eine Funktion vor](https://github.com/Navinspire-ia/navin/issues).
- Ergänze einen Anbieter, verbessere einen Skill oder entwickle eine Integration beziehungsweise einen Evaluationsfall.
- Verbessere eine Übersetzung oder teile einen nachvollziehbaren Workflow.
- Lies [CONTRIBUTING.md](./CONTRIBUTING.md) und das [CLA](./CLA.md), bevor du einen Pull Request einreichst.

Entwickelt von [Navinspire IA](https://navinspire.ai), gepflegt von [@aymenghad](https://github.com/aymenghad) und den [Navin-Mitwirkenden](https://github.com/Navinspire-ia/navin/graphs/contributors).

## Lizenz

[AGPL-3.0](./LICENSE), mit [kommerzieller Lizenzoption](./COMMERCIAL_LICENSE.md) von Navinspire IA. Beiträge unterliegen dem [Contributor License Agreement](./CLA.md).

<div align="center">

**Gib deinem nächsten Projekt mehr Autonomie.**

[Navin installieren](#loslegen) · [Dokumentation](./docs/README.md) · [Einen Stern vergeben](https://github.com/Navinspire-ia/navin)

Wenn Navin dir hilft, erleichtert ein Stern anderen die Entdeckung des Projekts.

</div>
