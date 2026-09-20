<div align="center">

<img src="./assets/navin-mark.svg" alt="Navin" width="64" height="64">

# 让目标变成完成的工作。

**开源 AI 智能体工作空间：编写代码、开展研究、调用工具，并在不同会话之间保留上下文。**

<p>
  <a href="./docs/readme-demo.md"><img src="./assets/readme/desktop-demo.gif" alt="Navin Desktop 修复折扣计算错误，并通过全部六项测试" width="100%"></a>
  <br>
  <sub>真实 Navin Desktop 会话的精简录屏：检查 → 修复 → 六项测试全部通过。 <a href="./docs/readme-demo.md">静态截图与过程说明</a></sub>
</p>

在终端、浏览器或桌面应用中运行。自由选择模型，始终掌握控制权。

[English](./README.md) · [Français](./README.fr.md) · [العربية](./README.ar.md) · [Español](./README.es.md) · [Português](./README.pt-BR.md) · [Deutsch](./README.de.md) · [简体中文](./README.zh-CN.md) · [日本語](./README.ja.md) · [한국어](./README.ko.md) · [Svenska](./README.sv.md)

[![AGPL-3.0 许可证](https://img.shields.io/badge/license-AGPL--3.0-66d9b0?style=flat-square)](./LICENSE) [![本地优先](https://img.shields.io/badge/local-first-5599ff?style=flat-square)](./docs/configuration.md) [![GitHub stars](https://img.shields.io/github/stars/Navinspire-ia/navin?style=flat-square&color=ffd166)](https://github.com/Navinspire-ia/navin/stargazers)

**[快速开始](#快速开始)** · **[下载桌面应用](https://navin.live/download)** · **[阅读文档](./docs/README.md)** · **[参与贡献](./CONTRIBUTING.md)**

</div>

## 给 Navin 一个任务

> “找到这个问题的根本原因，修复它，运行相关测试，并解释修改内容。”

> “研究这些竞争对手，附上来源比较他们的产品方案，再整理成报告。”

> “持续关注这个项目，在出现需要我处理的变化时通知我。”

Navin 将模型连接到文件、终端、浏览器和工具。它可以制定计划，将明确的子任务交给子智能体，检查结果，并在设定的限制内继续工作。

**一次对话结束后，工作仍能留下积累：** 项目知识、决策、可复用技能，以及后续任务需要的上下文。

## 快速开始

**Navin Desktop (Windows / macOS / Linux)**

[下载适用于 Windows、macOS 或 Linux 的 Navin Desktop](https://navin.live/download)

**CLI**

**Linux / macOS / WSL**

```bash
curl https://navin.live/install -fsS | bash
```

**Windows PowerShell**

```powershell
irm 'https://navin.live/install?win32=true' | iex
```

在项目目录中打开终端：

```bash
cd your-project
navin-cli
```

1. 按 **Ctrl+G** 打开 **Settings**，配置自己的 API 密钥或本地模型服务地址。
2. 选择模型和模式：**Ask**、**Plan**、**Agent**、**Review**、**Security** 或 **Debug**。
3. 试试：**“阅读这个项目，解释它的工作方式，然后提出一项有价值的改进。”**

软件可在开源许可证条款下免费使用。外部模型 API 和其他服务可能收取使用费用。[安装与故障排查](./docs/Installation.md)。

### 从源码安装 (gateway + WebUI)

在自己的设备上运行或修改 Navin：

```bash
git clone https://github.com/Navinspire-ia/navin.git
cd navin
make install
make start
```

`make install` 会在支持的环境中安装缺少的系统软件包、Python 后端和 WebUI。`make start` 启动网关和 WebUI，访问地址为 [localhost:5173](http://localhost:5173)。

如果跳过系统安装（`NAVIN_SKIP_SYSTEM=1` 或 `sh scripts/install.sh --no-system`），请先安装 **Python 3.11+、Git、Make、Node.js 18+ 和 npm**。

Linux/macOS 上的原生沙箱还需要 **rustup**（`make native`）。

在仓库中启动 CLI：`.venv/bin/navin-cli`；Windows 使用 `.venv\Scripts\navin-cli`。[完整安装指南](./docs/Installation.md)。

## 一个工作空间，多种工作方式

| 你的目标 | Navin 提供的能力 |
| --- | --- |
| **开发软件** | 探索仓库、编辑代码、终端、Git、浏览器预览、测试与代码审查。 |
| **理解代码库** | 项目图谱、代码索引、定义与引用查询、变更影响分析。 |
| **研究与数据提取** | 网页搜索、浏览器工具、网页抓取、结构化提取与附带来源的报告。 |
| **制作交付物** | 文档、演示文稿、电子表格、架构图和多媒体工作流。 |
| **开展业务增长工作** | 潜在客户研究与信息补全、SEO、营销研究和活动准备。 |
| **处理专业任务** | 招标分析、求职研究、会议转写、笔记与行动事项。 |
| **分解复杂任务** | 并行工作的子智能体，将结果汇总给主智能体。 |
| **持续推进目标** | 目标循环、定时任务，以及网关运行期间的 Heartbeat 检查。 |

部分工作流需要额外依赖、已配置的集成或兼容模型。具体设置请参阅[能力总览](./docs/capabilities.md)。

<p align="center">
  <a href="./docs/readme-demo.md#terminal"><img src="./assets/readme/cli-demo.gif" alt="Navin CLI 修复折扣计算错误，并通过全部六项测试" width="100%"></a>
  <br>
  <sub>真实 CLI 会话的精简录屏：检查 → 修复 → 六项测试全部通过。 <a href="./docs/readme-demo.md#terminal">静态截图与过程说明</a></sub>
</p>

## 为什么值得尝试 Navin

**上下文可以跨越对话。** 项目记忆和代码图谱帮助智能体找回决策并定位相关文件。可选的情景记忆支持回顾先前的工作。[记忆](./docs/memory.md) · [项目图谱](./docs/navin_dev/en/graph.md)

**工具让思考走向执行。** 文件、Shell、Git、浏览器、API 和 MCP 连接推理与实际操作。Skills 封装可复用的工作方法，插件和集成扩展工具能力。[配置 MCP](./docs/guides/configure-mcp-tools.md) · [Skills](./navin/skills/README.md)

**模型由你选择。** 接入 OpenAI、Anthropic、Google、Mistral、DeepSeek、Qwen 等服务，或通过 Ollama、LM Studio 和 vLLM 使用本地模型。为不同任务分配不同模型。实际能力取决于所选服务和模型。[配置](./docs/configuration.md)

**自主执行有明确边界。** 循环持续推进目标，Heartbeat 寻找值得跟进的工作。审批、资源预算、检查点和停止控制约束执行范围。[自动化](./docs/automations.md) · [安全](./SECURITY.md)

本地执行让工作空间保留在你的设备上。选择远程模型或接入外部服务时，请求所需的数据会发送给相应服务。

## 从目标到经过验证的结果

<p align="center">
<img src="./assets/readme/agent-workflow.svg" alt="目标、计划、权限、工具执行、验证与交付，并将经验反馈到项目记忆" width="100%">
</p>

[下载交互式流程图](./assets/readme/agent-workflow.html)，然后在浏览器中打开 HTML 文件。图中内容和控件使用英语。

## 能够从经验中学习的智能体

Navin 提供可选择启用的实验性学习机制：

| 机制 | 探索方向 |
| --- | --- |
| **Self-Evolve / Auto-Skills** | 将重复失败转化为候选技能，评估后决定采用或回滚。 |
| **World model** | 从记录的执行轨迹中，学习对工具结果的本地预测。 |
| **Policy learning** | 使用保留的评估案例，检验下一步行动建议。 |

这些机制**默认关闭**。启用需要通过评估；将技能发布给所有项目需要人工操作。这些机制不会微调对话模型。

项目的目标是构建能力不断增强的自主智能体。**Navin 不声称自己已经实现 AGI。**

[技能演化](./docs/skills-evolution.md) · [World model](./docs/world-model.md) · [Policy learning](./docs/policy.md)

## 连接你已经在用的工具

- **模型接入：** 自有 API 密钥或本地推理。
- **扩展方式：** MCP 服务器、Skills、插件和自定义工具。
- **消息渠道：** 通过配置集成接入 Telegram、Slack、Discord、WhatsApp、邮件、Teams 等。
- **使用界面：** CLI、WebUI 和可下载的桌面应用。

本仓库包含公开的智能体运行时、CLI、WebUI 及配套工具。桌面打包、navin.live 服务、网站基础设施和版本发布流程单独维护。

## 一起构建 Navin

试着完成一个真实任务，告诉我们智能体在哪些地方帮到了你，又在哪些地方遇到了困难。

- [提交可复现的问题或提出功能建议](https://github.com/Navinspire-ia/navin/issues)。
- 添加模型服务、改进 Skill、开发集成，或贡献评估案例。
- 改进翻译，或分享别人也能复现的工作流。
- 提交 Pull Request 前，请阅读[贡献指南](./CONTRIBUTING.md)和 [CLA](./CLA.md)。

由 [Navinspire IA](https://navinspire.ai) 创建，由 [@aymenghad](https://github.com/aymenghad) 和 [Navin 贡献者](https://github.com/Navinspire-ia/navin/graphs/contributors)共同维护。

## 许可证

采用 [AGPL-3.0](./LICENSE)，Navinspire IA 同时提供[商业许可选项](./COMMERCIAL_LICENSE.md)。贡献遵循[贡献者许可协议](./CLA.md)。

<div align="center">

**让你的下一个项目拥有更多自主能力。**

[安装 Navin](#快速开始) · [阅读文档](./docs/README.md) · [给项目一颗 Star](https://github.com/Navinspire-ia/navin)

如果 Navin 帮到了你，一颗 Star 能让更多人发现它。

</div>
