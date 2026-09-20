<div align="center">

<img src="./assets/readme/hero.svg" alt="Navin: sua IA, sua máquina. Desenvolva, pesquise e automatize." width="100%">

# Transforme um objetivo em trabalho entregue.

**Um espaço de trabalho de agentes de IA de código aberto para programar, pesquisar, usar ferramentas e manter o contexto entre sessões.**

No terminal, no navegador ou no aplicativo desktop. Escolha seus modelos. Mantenha o controle.

[English](./README.md) · [Français](./README.fr.md) · [العربية](./README.ar.md) · [Español](./README.es.md) · [Português](./README.pt-BR.md) · [Deutsch](./README.de.md) · [简体中文](./README.zh-CN.md)

[![Licença AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-66d9b0?style=flat-square)](./LICENSE) [![Execução local](https://img.shields.io/badge/local-first-5599ff?style=flat-square)](./docs/configuration.md) [![GitHub stars](https://img.shields.io/github/stars/Navinspire-ia/navin?style=flat-square&color=ffd166)](https://github.com/Navinspire-ia/navin/stargazers)

**[Começar](#começar)** · **[Baixar o aplicativo](https://navin.live/download)** · **[Documentação](./docs/README.md)** · **[Contribuir](./CONTRIBUTING.md)**

</div>

## Dê uma missão ao Navin

> "Encontre a causa deste bug, corrija, execute os testes relevantes e explique as mudanças."

> "Pesquise estes concorrentes, compare suas ofertas com fontes e prepare um relatório."

> "Monitore este projeto e me avise quando alguma mudança precisar da minha atenção."

O Navin conecta o modelo aos seus arquivos, terminal, navegador e ferramentas. Ele pode planejar, delegar tarefas específicas a subagentes, verificar resultados e continuar dentro dos limites configurados.

**O trabalho deixa algo que pode ser reaproveitado:** conhecimento do projeto, decisões, habilidades e contexto.

## Começar

**Linux / macOS / WSL**

```bash
curl https://navin.live/install -fsS | bash
```

**Windows PowerShell**

```powershell
irm 'https://navin.live/install?win32=true' | iex
```

Abra um terminal no seu projeto:

```bash
cd your-project
navin-cli
```

1. Abra **Settings** com **Ctrl+G** e configure sua chave de API ou o endereço de um modelo local.
2. Escolha o modelo e o modo: **Ask**, **Plan**, **Agent**, **Review**, **Security** ou **Debug**.
3. Experimente: **"Leia este projeto e explique como ele funciona. Depois, sugira uma melhoria útil."**

Prefere uma janela? [Baixe o Navin Desktop para Windows, macOS ou Linux](https://navin.live/download).

O software é gratuito sob sua licença de código aberto. APIs de modelos e serviços externos podem cobrar pelo uso. [Instalação e solução de problemas](./docs/Installation.md).

<details>
<summary><strong>Instalar a partir do código-fonte</strong></summary>

```bash
git clone https://github.com/Navinspire-ia/navin.git
cd navin
make install
make start
```

WebUI de desenvolvimento: [localhost:5173](http://localhost:5173). CLI no repositório: `.venv/bin/navin-cli`; no Windows: `.venv\Scripts\navin-cli`. Requisitos e build de produção: [guia de instalação](./docs/Installation.md).

</details>

## Um espaço para muitos tipos de trabalho

| Seu objetivo | O que o Navin oferece |
| --- | --- |
| **Desenvolver software** | Exploração do repositório, edição de código, terminal, Git, prévia no navegador, testes e revisão. |
| **Entender o projeto** | Grafo do projeto, índice de código, definições, referências e análise de impacto. |
| **Pesquisar e extrair dados** | Busca na web, navegador, scraping, extração estruturada e relatórios com fontes. |
| **Criar entregáveis** | Documentos, apresentações, planilhas, diagramas de arquitetura e fluxos multimídia. |
| **Desenvolver um negócio** | Prospecção, enriquecimento de leads, SEO, pesquisa de marketing e preparação de campanhas. |
| **Resolver tarefas profissionais** | Análise de licitações, busca de oportunidades, transcrição de reuniões, notas e ações. |
| **Delegar tarefas complexas** | Subagentes que trabalham em paralelo e devolvem resultados ao agente principal. |
| **Manter o trabalho avançando** | Ciclos por objetivo, tarefas agendadas e verificações Heartbeat enquanto o gateway está ativo. |

Alguns fluxos exigem dependências adicionais, integrações configuradas ou um modelo compatível. Consulte o [mapa de capacidades](./docs/capabilities.md).

<p align="center">
<img src="./assets/readme/cli.png" alt="Navin CLI com um exemplo ilustrativo de exploração de projeto" width="100%">
<br><sub>Prévia do CLI com um exemplo de exploração de projeto.</sub>
</p>

## Por que explorar o Navin

**Contexto que continua depois da conversa.** A memória do projeto e o grafo de código ajudam o agente a recuperar decisões e encontrar arquivos relevantes. A memória episódica opcional permite recordar trabalhos anteriores. [Memória](./docs/memory.md) · [Grafo](./docs/navin_dev/en/graph.md)

**Ferramentas que executam o trabalho.** Arquivos, shell, Git, navegador, APIs e MCP conectam raciocínio e ação. Skills reúnem métodos reutilizáveis; plugins e integrações ampliam as ferramentas. [Configurar MCP](./docs/guides/configure-mcp-tools.md) · [Skills](./navin/skills/README.md)

**Você escolhe os modelos.** OpenAI, Anthropic, Google, Mistral, DeepSeek, Qwen e outros provedores, ou modelos locais com Ollama, LM Studio e vLLM. Use modelos diferentes para tarefas diferentes. As capacidades dependem do provedor e do modelo. [Configuração](./docs/configuration.md)

**Autonomia com controles claros.** Os ciclos mantêm um objetivo em andamento, e o Heartbeat identifica acompanhamentos úteis. Aprovações, limites de recursos, checkpoints e comandos de parada delimitam a execução. [Automações](./docs/automations.md) · [Segurança](./SECURITY.md)

A execução local mantém o espaço de trabalho na sua máquina. Ao escolher um modelo remoto ou serviço conectado, os dados necessários à solicitação são enviados a esse serviço.

## Do objetivo ao resultado verificado

<p align="center">
<img src="./assets/readme/agent-workflow.svg" alt="Objetivo, plano, permissões, ferramentas, verificação e entrega conectados à memória do projeto" width="100%">
</p>

[Baixe o diagrama interativo](./assets/readme/agent-workflow.html) e abra o HTML no navegador. O diagrama e seus controles estão em inglês.

## Um agente que pode aprender com a experiência

O Navin inclui camadas experimentais de aprendizado com ativação opcional:

| Camada | O que explora |
| --- | --- |
| **Self-Evolve / Auto-Skills** | Transformar falhas repetidas em habilidades candidatas, avaliá-las, promovê-las ou revertê-las. |
| **World model** | Aprender previsões locais dos resultados de ferramentas a partir de trajetórias registradas. |
| **Policy learning** | Avaliar sugestões da próxima ação com casos reservados para avaliação. |

Essas camadas ficam **desativadas por padrão**. A ativação depende de avaliações; publicar uma habilidade para todos os projetos exige uma ação humana. Elas não retreinam o modelo de conversa.

A ambição é criar agentes autônomos cada vez mais capazes. **O Navin não afirma ser uma AGI.**

[Evolução de Skills](./docs/skills-evolution.md) · [World model](./docs/world-model.md) · [Policy learning](./docs/policy.md)

## Conecte suas ferramentas

- **Modelos:** suas chaves de API ou inferência local.
- **Extensões:** servidores MCP, Skills, plugins e ferramentas próprias.
- **Canais:** Telegram, Slack, Discord, WhatsApp, email, Teams e outros por meio de integrações configuradas.
- **Interfaces:** CLI, WebUI e aplicativos desktop para download.

Este repositório contém o motor público do agente, CLI, WebUI e ferramentas relacionadas. O empacotamento desktop, o serviço navin.live, a infraestrutura do site e a publicação de versões são mantidos separadamente.

## Vamos construir o Navin juntos

Teste uma tarefa real e conte onde o agente ajudou e onde encontrou dificuldades.

- [Relate um bug reproduzível ou proponha uma funcionalidade](https://github.com/Navinspire-ia/navin/issues).
- Adicione um provedor, melhore uma Skill, crie uma integração ou um caso de avaliação.
- Melhore uma tradução ou compartilhe um fluxo que outras pessoas possam reproduzir.
- Leia [CONTRIBUTING.md](./CONTRIBUTING.md) e o [CLA](./CLA.md) antes de enviar uma pull request.

Criado pela [Navinspire IA](https://navinspire.ai), mantido por [@aymenghad](https://github.com/aymenghad) e pelos [contribuidores do Navin](https://github.com/Navinspire-ia/navin/graphs/contributors).

## Licença

[AGPL-3.0](./LICENSE), com [opção de licença comercial](./COMMERCIAL_LICENSE.md) da Navinspire IA. As contribuições seguem o [acordo de licença de contribuição](./CLA.md).

<div align="center">

**Dê mais autonomia ao seu próximo projeto.**

[Instalar o Navin](#começar) · [Documentação](./docs/README.md) · [Dar uma estrela](https://github.com/Navinspire-ia/navin)

Se o Navin ajudar você, uma estrela ajuda outras pessoas a descobri-lo.

</div>
