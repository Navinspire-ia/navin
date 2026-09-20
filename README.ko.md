<div align="center">

<img src="./assets/navin-mark.svg" alt="Navin" width="64" height="64">

# 목표를 완료된 작업으로 바꾸세요.

**코드를 작성하고, 조사하고, 도구를 사용하며, 세션이 바뀌어도 맥락을 이어가는 오픈 소스 AI 에이전트 작업 공간입니다.**

<p>
  <a href="./docs/readme-demo.md"><img src="./assets/readme/desktop-demo.gif" alt="Navin Desktop가 할인 계산 오류를 수정하고 테스트 6개를 모두 통과하는 모습" width="100%"></a>
  <br>
  <sub>실제 Navin Desktop 세션을 축약한 녹화: 확인 → 수정 → 테스트 6개 통과. <a href="./docs/readme-demo.md">정지 이미지와 설명</a></sub>
</p>

터미널, 브라우저 또는 데스크톱 앱에서 실행하세요. 모델을 직접 선택하고 제어권을 유지하세요.

[English](./README.md) · [Français](./README.fr.md) · [العربية](./README.ar.md) · [Español](./README.es.md) · [Português](./README.pt-BR.md) · [Deutsch](./README.de.md) · [简体中文](./README.zh-CN.md) · [日本語](./README.ja.md) · [한국어](./README.ko.md) · [Svenska](./README.sv.md)

[![AGPL-3.0 라이선스](https://img.shields.io/badge/license-AGPL--3.0-66d9b0?style=flat-square)](./LICENSE) [![로컬 우선](https://img.shields.io/badge/local-first-5599ff?style=flat-square)](./docs/configuration.md) [![GitHub stars](https://img.shields.io/github/stars/Navinspire-ia/navin?style=flat-square&color=ffd166)](https://github.com/Navinspire-ia/navin/stargazers)

**[시작하기](#시작하기)** · **[데스크톱 앱 다운로드](https://navin.live/download)** · **[문서](./docs/README.md)** · **[기여하기](./CONTRIBUTING.md)**

</div>

## Navin에게 작업을 맡겨보세요

> "이 버그의 원인을 찾아 수정하고, 관련 테스트를 실행한 다음 변경 사항을 설명해 줘."

> "이 경쟁사들을 조사하고, 출처를 포함해 서비스를 비교한 뒤 보고서로 정리해 줘."

> "이 프로젝트를 살펴보다가 내가 대응해야 할 변화가 생기면 알려 줘."

Navin은 모델을 파일, 터미널, 브라우저와 도구에 연결합니다. 작업을 계획하고, 명확한 하위 작업을 서브에이전트에 위임하며, 결과를 확인하고 설정된 한도 안에서 작업을 이어갈 수 있습니다.

**대화가 끝나도 다음 작업에 활용할 수 있는 것이 남습니다.** 프로젝트 지식, 결정 사항, 재사용 가능한 스킬, 작업의 맥락입니다.

## 시작하기

**Navin Desktop (Windows / macOS / Linux)**

[Windows, macOS 또는 Linux용 Navin Desktop을 다운로드](https://navin.live/download)

**CLI**

**Linux / macOS / WSL**

```bash
curl https://navin.live/install -fsS | bash
```

**Windows PowerShell**

```powershell
irm 'https://navin.live/install?win32=true' | iex
```

프로젝트 폴더에서 터미널을 여세요.

```bash
cd your-project
navin-cli
```

1. **Ctrl+G**로 **Settings**를 열고 API 키 또는 로컬 모델의 접속 주소를 설정하세요.
2. 모델과 모드를 선택하세요: **Ask**, **Plan**, **Agent**, **Review**, **Security**, **Debug**.
3. 먼저 **"이 프로젝트를 읽고 작동 방식을 설명해 줘. 그런 다음 유용한 개선 사항 하나를 제안해 줘."**라고 요청해 보세요.

소프트웨어는 오픈 소스 라이선스 조건에 따라 무료로 사용할 수 있습니다. 외부 모델 API와 연결된 서비스에는 사용 요금이 발생할 수 있습니다. [설치 및 문제 해결](./docs/Installation.md).

### 소스에서 설치하기 (gateway + WebUI)

내 컴퓨터에서 Navin을 실행하거나 수정하려면:

```bash
git clone https://github.com/Navinspire-ia/navin.git
cd navin
make install
make start
```

`make install`은 지원되는 환경에서 누락된 시스템 패키지, Python 백엔드와 WebUI를 설치합니다. `make start`는 게이트웨이와 WebUI를 시작합니다. [localhost:5173](http://localhost:5173)에서 접속하세요.

시스템 설치를 건너뛰는 경우(`NAVIN_SKIP_SYSTEM=1` 또는 `sh scripts/install.sh --no-system`), 먼저 **Python 3.11+, Git, Make, Node.js 18+, npm**을 설치하세요.

Linux/macOS에서 네이티브 샌드박스를 사용하려면 **rustup**도 필요합니다(`make native`).

저장소에서 CLI 실행: `.venv/bin/navin-cli`(Windows: `.venv\Scripts\navin-cli`). [전체 설치 가이드](./docs/Installation.md).

## 하나의 작업 공간에서 다양한 일을

| 하고 싶은 일 | Navin이 제공하는 기능 |
| --- | --- |
| **소프트웨어 개발** | 저장소 탐색, 코드 편집, 터미널, Git, 브라우저 미리보기, 테스트와 리뷰. |
| **코드베이스 이해** | 프로젝트 그래프, 코드 인덱스, 정의와 참조 검색, 변경 영향 분석. |
| **조사 및 데이터 추출** | 웹 검색, 브라우저 도구, 스크래핑, 구조화된 추출, 출처가 포함된 보고서. |
| **결과물 제작** | 문서, 프레젠테이션, 스프레드시트, 아키텍처 다이어그램, 미디어 작업 흐름. |
| **사업 성장 지원** | 잠재 고객 조사와 정보 보완, SEO, 마케팅 조사, 캠페인 준비. |
| **전문 업무 처리** | 입찰 분석, 채용 기회 조사, 회의 전사, 메모와 실행 항목 정리. |
| **복잡한 작업 분담** | 서브에이전트가 병렬로 작업하고 메인 에이전트에 결과를 전달합니다. |
| **지속적인 작업 진행** | 목표 기반 루프, 예약 작업, 게이트웨이가 실행되는 동안의 Heartbeat 확인. |

일부 작업에는 추가 의존성, 설정된 연동 또는 호환 모델이 필요합니다. 자세한 내용은 [기능 안내](./docs/capabilities.md)를 확인하세요.

<p align="center">
  <a href="./docs/readme-demo.md#terminal"><img src="./assets/readme/cli-demo.gif" alt="Navin CLI가 할인 계산 오류를 수정하고 테스트 6개를 모두 통과하는 모습" width="100%"></a>
  <br>
  <sub>실제 CLI 세션을 축약한 녹화: 확인 → 수정 → 테스트 6개 통과. <a href="./docs/readme-demo.md#terminal">정지 이미지와 설명</a></sub>
</p>

## Navin을 살펴볼 이유

**대화 이후에도 이어지는 맥락.** 프로젝트 메모리와 코드 그래프는 에이전트가 결정 사항을 되짚고 관련 파일을 찾도록 돕습니다. 선택적으로 활성화할 수 있는 에피소드 메모리는 이전 작업을 회상하는 데 쓰입니다. [메모리](./docs/memory.md) · [프로젝트 그래프](./docs/navin_dev/en/graph.md)

**생각을 실행으로 연결하는 도구.** 파일, 셸, Git, 브라우저, API와 MCP가 추론을 실제 작업에 연결합니다. Skills는 재사용 가능한 작업 방법을 담고, 플러그인과 연동은 도구를 확장합니다. [MCP 설정](./docs/guides/configure-mcp-tools.md) · [Skills](./navin/skills/README.md)

**모델을 직접 선택하세요.** OpenAI, Anthropic, Google, Mistral, DeepSeek, Qwen 등의 서비스 또는 Ollama, LM Studio, vLLM을 통한 로컬 모델을 연결하세요. 작업마다 다른 모델을 배정할 수 있습니다. 지원되는 기능은 제공업체와 모델에 따라 달라집니다. [설정](./docs/configuration.md)

**명확한 한도 안에서 자율 실행.** 루프는 목표를 계속 추진하고, Heartbeat는 유용한 후속 작업을 찾습니다. 승인, 리소스 예산, 체크포인트와 중지 기능이 실행 범위를 관리합니다. [자동화](./docs/automations.md) · [보안](./SECURITY.md)

로컬 실행에서는 작업 공간이 사용자의 컴퓨터에 유지됩니다. 원격 모델이나 외부 서비스를 선택하면 요청에 필요한 데이터가 해당 서비스로 전송됩니다.

## 목표에서 검증된 결과까지

<p align="center">
<img src="./assets/readme/agent-workflow.svg" alt="목표, 계획, 권한, 도구 실행, 검증과 결과 전달, 그리고 프로젝트 메모리에 축적되는 경험" width="100%">
</p>

[대화형 다이어그램을 다운로드](./assets/readme/agent-workflow.html)하고 브라우저에서 HTML 파일을 여세요. 다이어그램과 조작 메뉴는 영어로 제공됩니다.

## 경험에서 배울 수 있는 에이전트

Navin에는 선택적으로 활성화할 수 있는 실험적 학습 기능이 있습니다.

| 기능 | 탐구하는 방향 |
| --- | --- |
| **Self-Evolve / Auto-Skills** | 반복되는 실패를 스킬 후보로 만들고 평가한 뒤 채택하거나 롤백합니다. |
| **World model** | 기록된 실행 이력을 바탕으로 도구 결과를 예측하는 방법을 로컬에서 학습합니다. |
| **Policy learning** | 평가용으로 분리한 사례를 사용해 다음 행동 제안을 검증합니다. |

이 기능들은 **기본적으로 꺼져 있습니다**. 활성화에는 평가 기준이 적용되며, 모든 프로젝트에 스킬을 배포하려면 사람의 조작이 필요합니다. 대화 모델 자체를 미세 조정하는 기능은 아닙니다.

목표는 더 많은 일을 해낼 수 있는 자율 에이전트를 만드는 것입니다. **Navin은 이미 AGI를 달성했다고 주장하지 않습니다.**

[스킬 진화](./docs/skills-evolution.md) · [World model](./docs/world-model.md) · [Policy learning](./docs/policy.md)

## 이미 쓰고 있는 도구와 연결하세요

- **모델:** 개인 API 키 또는 로컬 추론.
- **확장:** MCP 서버, Skills, 플러그인과 사용자 정의 도구.
- **채널:** 설정된 연동을 통해 Telegram, Slack, Discord, WhatsApp, 이메일, Teams 등에 연결.
- **인터페이스:** CLI, WebUI와 다운로드 가능한 데스크톱 앱.

이 저장소에는 공개 에이전트 런타임, CLI, WebUI와 관련 도구가 포함됩니다. 데스크톱 패키징, navin.live 서비스, 웹사이트 인프라와 릴리스 배포는 별도로 관리됩니다.

## 함께 Navin을 만들어가요

실제 작업에 사용해 보고, 에이전트가 어디에서 도움이 되었고 어디에서 막혔는지 알려주세요.

- [재현 가능한 버그를 신고하거나 기능을 제안하세요](https://github.com/Navinspire-ia/navin/issues).
- 제공업체를 추가하고, Skill을 개선하거나, 연동 및 평가 사례를 만들어보세요.
- 번역을 개선하거나 다른 사람도 재현할 수 있는 작업 흐름을 공유하세요.
- 풀 리퀘스트를 제출하기 전에 [기여 가이드](./CONTRIBUTING.md)와 [CLA](./CLA.md)를 읽어주세요.

[Navinspire IA](https://navinspire.ai)가 만들고, [@aymenghad](https://github.com/aymenghad)와 [Navin 기여자](https://github.com/Navinspire-ia/navin/graphs/contributors)가 유지보수합니다.

## 라이선스

[AGPL-3.0](./LICENSE)을 따르며, Navinspire IA는 [상용 라이선스](./COMMERCIAL_LICENSE.md)도 제공합니다. 기여에는 [기여자 라이선스 계약](./CLA.md)이 적용됩니다.

<div align="center">

**다음 프로젝트에 더 많은 자율성을 더하세요.**

[Navin 설치](#시작하기) · [문서 읽기](./docs/README.md) · [Star 남기기](https://github.com/Navinspire-ia/navin)

Navin이 도움이 되었다면, Star를 남겨 다른 사람들도 발견할 수 있도록 도와주세요.

</div>
