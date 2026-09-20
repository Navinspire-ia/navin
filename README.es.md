<div align="center">

<img src="./assets/navin-mark.svg" alt="Navin" width="64" height="64">

# Convierte un objetivo en trabajo hecho.

**Un espacio de trabajo de agentes de IA de código abierto para programar, investigar, utilizar herramientas y conservar el contexto entre sesiones.**

<p>
  <a href="./docs/readme-demo.md"><img src="./assets/readme/desktop-demo.gif" alt="Navin Desktop corrige un error de descuento y supera las seis pruebas" width="100%"></a>
  <br>
  <sub>Sesión real de Navin Desktop, abreviada: revisar → corregir → 6 pruebas superadas. <a href="./docs/readme-demo.md">Imagen fija y explicación</a></sub>
</p>

En tu terminal, navegador o aplicación de escritorio. Elige tus modelos. Mantén el control.

[English](./README.md) · [Français](./README.fr.md) · [العربية](./README.ar.md) · [Español](./README.es.md) · [Português](./README.pt-BR.md) · [Deutsch](./README.de.md) · [简体中文](./README.zh-CN.md) · [日本語](./README.ja.md) · [한국어](./README.ko.md) · [Svenska](./README.sv.md)

[![Licencia AGPL-3.0](https://img.shields.io/badge/licencia-AGPL--3.0-66d9b0?style=flat-square)](./LICENSE) [![Ejecución local](https://img.shields.io/badge/local-first-5599ff?style=flat-square)](./docs/configuration.md) [![GitHub stars](https://img.shields.io/github/stars/Navinspire-ia/navin?style=flat-square&color=ffd166)](https://github.com/Navinspire-ia/navin/stargazers)

**[Empezar](#empezar)** · **[Descargar la aplicación](https://navin.live/download)** · **[Documentación](./docs/README.md)** · **[Contribuir](./CONTRIBUTING.md)**

</div>

## Dale una misión a Navin

> «Encuentra la causa de este error, corrígelo, ejecuta las pruebas relevantes y explica los cambios».

> «Investiga estos competidores, compara sus ofertas con fuentes y prepara un informe».

> «Supervisa este proyecto y avísame cuando un cambio necesite mi atención».

Navin conecta el modelo con tus archivos, terminal, navegador y herramientas. Puede planificar, delegar tareas concretas a subagentes, comprobar los resultados y continuar dentro de los límites configurados.

**El trabajo deja algo que se puede reutilizar:** conocimiento del proyecto, decisiones, habilidades y contexto.

## Empezar

**Navin Desktop (Windows / macOS / Linux)**

[Descarga Navin Desktop para Windows, macOS o Linux](https://navin.live/download)

**CLI**

**Linux / macOS / WSL**

```bash
curl https://navin.live/install -fsS | bash
```

**Windows PowerShell**

```powershell
irm 'https://navin.live/install?win32=true' | iex
```

Abre una terminal en tu proyecto:

```bash
cd your-project
navin-cli
```

1. Abre **Settings** con **Ctrl+G** y configura tu clave API o la dirección de un modelo local.
2. Elige el modelo y el modo: **Ask**, **Plan**, **Agent**, **Review**, **Security** o **Debug**.
3. Prueba: **«Lee este proyecto y explica cómo funciona. Después, sugiere una mejora útil»**.

El software es gratuito bajo su licencia de código abierto. Las API de modelos y los servicios externos pueden cobrar por su uso. [Instalación y ayuda](./docs/Installation.md).

### Desde el código fuente (gateway + WebUI)

Para ejecutar o modificar Navin en tu equipo:

```bash
git clone https://github.com/Navinspire-ia/navin.git
cd navin
make install
make start
```

`make install` instala los paquetes del sistema que faltan cuando es posible, el backend Python y la WebUI. `make start` inicia el gateway y la WebUI en [localhost:5173](http://localhost:5173).

Si omites la instalación del sistema (`NAVIN_SKIP_SYSTEM=1` o `sh scripts/install.sh --no-system`), instala primero **Python 3.11+, Git, Make, Node.js 18+ y npm**.

En Linux/macOS, el entorno aislado nativo también necesita **rustup** (`make native`).

CLI desde el repositorio: `.venv/bin/navin-cli` (Windows: `.venv\Scripts\navin-cli`). [Guía completa de instalación](./docs/Installation.md).

## Un espacio para muchos tipos de trabajo

| Tu objetivo | Qué aporta Navin |
| --- | --- |
| **Desarrollar software** | Exploración del repositorio, edición de código, terminal, Git, vista previa en navegador, pruebas y revisión. |
| **Entender el proyecto** | Grafo del proyecto, índice de código, definiciones, referencias y análisis de impacto. |
| **Investigar y extraer datos** | Búsqueda web, navegador, scraping, extracción estructurada e informes con fuentes. |
| **Crear entregables** | Documentos, presentaciones, hojas de cálculo, diagramas de arquitectura y flujos multimedia. |
| **Impulsar un negocio** | Prospección, enriquecimiento de contactos, SEO, investigación de marketing y preparación de campañas. |
| **Resolver tareas profesionales** | Análisis de licitaciones, búsqueda de empleo, transcripción de reuniones, notas y acciones. |
| **Delegar tareas complejas** | Subagentes que trabajan en paralelo y devuelven sus resultados al agente principal. |
| **Mantener el avance** | Bucles por objetivos, tareas programadas y comprobaciones Heartbeat mientras el gateway está activo. |

Algunos flujos necesitan dependencias adicionales, integraciones configuradas o un modelo compatible. Consulta el [mapa de capacidades](./docs/capabilities.md).

<p align="center">
  <a href="./docs/readme-demo.md#terminal"><img src="./assets/readme/cli-demo.gif" alt="Navin CLI corrige un error de descuento y supera las seis pruebas" width="100%"></a>
  <br>
  <sub>Sesión real de CLI, abreviada: revisar → corregir → 6 pruebas superadas. <a href="./docs/readme-demo.md#terminal">Imagen fija y explicación</a></sub>
</p>

## Por qué explorar Navin

**Contexto que sobrevive a la conversación.** La memoria del proyecto y el grafo de código ayudan a recuperar decisiones y localizar archivos relevantes. La memoria episódica opcional permite recordar trabajos anteriores. [Memoria](./docs/memory.md) · [Grafo](./docs/navin_dev/en/graph.md)

**Herramientas que ejecutan el trabajo.** Archivos, shell, Git, navegador, API y MCP conectan el razonamiento con la acción. Las Skills reúnen métodos reutilizables; los plugins e integraciones amplían las herramientas. [Configurar MCP](./docs/guides/configure-mcp-tools.md) · [Skills](./navin/skills/README.md)

**Tú eliges los modelos.** OpenAI, Anthropic, Google, Mistral, DeepSeek, Qwen y otros proveedores, o modelos locales con Ollama, LM Studio y vLLM. Asigna distintos modelos a distintas tareas. Las capacidades dependen del proveedor y del modelo. [Configuración](./docs/configuration.md)

**Autonomía con controles explícitos.** Los bucles mantienen un objetivo en marcha y Heartbeat detecta seguimientos útiles. Aprobaciones, presupuestos de recursos, puntos de control y opciones de parada delimitan la ejecución. [Automatizaciones](./docs/automations.md) · [Seguridad](./SECURITY.md)

La ejecución local mantiene el espacio de trabajo en tu equipo. Si eliges un modelo remoto o un servicio conectado, los datos necesarios para la solicitud se envían a ese servicio.

## Del objetivo al resultado verificado

<p align="center">
<img src="./assets/readme/agent-workflow.svg" alt="Objetivo, plan, permisos, herramientas, verificación y entrega, conectados con la memoria del proyecto" width="100%">
</p>

[Descarga el diagrama interactivo](./assets/readme/agent-workflow.html) y abre el HTML en tu navegador. El diagrama y sus controles están en inglés.

## Un agente que puede aprender de la experiencia

Navin incluye capas experimentales de aprendizaje que puedes activar:

| Capa | Qué explora |
| --- | --- |
| **Self-Evolve / Auto-Skills** | Convertir fallos repetidos en habilidades candidatas, evaluarlas, promoverlas o revertirlas. |
| **World model** | Aprender predicciones locales sobre los resultados de herramientas a partir de trayectorias registradas. |
| **Policy learning** | Evaluar sugerencias de la siguiente acción con casos reservados para evaluación. |

Están **desactivadas por defecto**. Su activación depende de evaluaciones; publicar una habilidad para todos los proyectos requiere una acción humana. No reentrenan el modelo de conversación.

La ambición es crear agentes autónomos cada vez más capaces. **Navin no afirma ser una AGI.**

[Evolución de Skills](./docs/skills-evolution.md) · [World model](./docs/world-model.md) · [Policy learning](./docs/policy.md)

## Conecta tus herramientas

- **Modelos:** tus claves API o inferencia local.
- **Extensiones:** servidores MCP, Skills, plugins y herramientas propias.
- **Canales:** Telegram, Slack, Discord, WhatsApp, correo, Teams y más mediante integraciones configuradas.
- **Interfaces:** CLI, WebUI y aplicaciones de escritorio descargables.

Este repositorio incluye el motor público del agente, CLI, WebUI y herramientas relacionadas. El empaquetado de escritorio, navin.live, la infraestructura del sitio y la publicación de versiones se mantienen por separado.

## Construyamos Navin juntos

Prueba una tarea real y cuéntanos dónde te ayudó el agente y dónde se atascó.

- [Reporta un error reproducible o propone una función](https://github.com/Navinspire-ia/navin/issues).
- Añade un proveedor, mejora una Skill, crea una integración o un caso de evaluación.
- Mejora una traducción o comparte un flujo reproducible.
- Lee [CONTRIBUTING.md](./CONTRIBUTING.md) y el [CLA](./CLA.md) antes de enviar una pull request.

Creado por [Navinspire IA](https://navinspire.ai), mantenido por [@aymenghad](https://github.com/aymenghad) y los [contribuidores de Navin](https://github.com/Navinspire-ia/navin/graphs/contributors).

## Licencia

[AGPL-3.0](./LICENSE), con [licencia comercial opcional](./COMMERCIAL_LICENSE.md) de Navinspire IA. Las contribuciones siguen el [acuerdo de licencia del contribuidor](./CLA.md).

<div align="center">

**Dale más autonomía a tu próximo proyecto.**

[Instalar Navin](#empezar) · [Documentación](./docs/README.md) · [Dar una estrella](https://github.com/Navinspire-ia/navin)

Si Navin te ayuda, una estrella facilita que otras personas lo descubran.

</div>
