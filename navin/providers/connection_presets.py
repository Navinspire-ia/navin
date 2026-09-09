# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Native connection profiles: region, plan and protocol pick the API base.

Chinese vendors do not share one key or one host. A DashScope China key
fails on the Singapore host; a Z.AI coding-plan key fails on the general
PaaS URL. Settings fills the base URL from this table instead of asking
the user to guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ProviderRegion = Literal["china", "international", "singapore", "us"]
ProviderPlan = Literal["payg", "token", "coding"]
ProviderProtocol = Literal["openai", "anthropic", "native"]

# Lookup key: region|plan|protocol. ``*`` is a wildcard for that slot.
_Key = tuple[str, str, str]


def _key(region: str, plan: str, protocol: str) -> str:
    return f"{region}|{plan}|{protocol}"


@dataclass(frozen=True)
class ConnectionChoice:
    id: str
    label: str


@dataclass(frozen=True)
class ConnectionPreset:
    provider: str
    regions: tuple[ConnectionChoice, ...]
    plans: tuple[ConnectionChoice, ...]
    protocols: tuple[ConnectionChoice, ...]
    default_region: str
    default_plan: str
    default_protocol: str
    bases: dict[str, str]
    docs_url: str = ""
    notes: str = ""


_REGIONS = {
    "china": ConnectionChoice("china", "China"),
    "international": ConnectionChoice("international", "International"),
    "singapore": ConnectionChoice("singapore", "Singapore"),
    "us": ConnectionChoice("us", "USA"),
}
_PLANS = {
    "payg": ConnectionChoice("payg", "Pay-as-you-go"),
    "token": ConnectionChoice("token", "Token plan"),
    "coding": ConnectionChoice("coding", "Coding plan"),
}
_PROTOCOLS = {
    "openai": ConnectionChoice("openai", "OpenAI compatible"),
    "anthropic": ConnectionChoice("anthropic", "Anthropic compatible"),
    "native": ConnectionChoice("native", "Native API"),
}


def _choices(mapping: dict[str, ConnectionChoice], ids: tuple[str, ...]) -> tuple[ConnectionChoice, ...]:
    return tuple(mapping[item] for item in ids)


def _preset(
    provider: str,
    *,
    regions: tuple[str, ...],
    plans: tuple[str, ...],
    protocols: tuple[str, ...],
    default_region: str,
    default_plan: str,
    default_protocol: str,
    bases: dict[str, str],
    docs_url: str = "",
    notes: str = "",
) -> ConnectionPreset:
    return ConnectionPreset(
        provider=provider,
        regions=_choices(_REGIONS, regions),
        plans=_choices(_PLANS, plans),
        protocols=_choices(_PROTOCOLS, protocols),
        default_region=default_region,
        default_plan=default_plan,
        default_protocol=default_protocol,
        bases=bases,
        docs_url=docs_url,
        notes=notes,
    )


CONNECTION_PRESETS: dict[str, ConnectionPreset] = {
    "qwen": _preset(
        "qwen",
        regions=("china", "singapore", "us"),
        plans=("payg", "token", "coding"),
        protocols=("openai", "native"),
        default_region="singapore",
        default_plan="payg",
        default_protocol="openai",
        docs_url="https://www.alibabacloud.com/help/en/model-studio/developer-reference/use-qwen-by-calling-api",
        notes=(
            "Keys are bound to a region and a plan. A China Model Studio key "
            "does not work on Singapore or USA. Native DashScope is required "
            "for some audio and image calls."
        ),
        bases={
            _key("china", "*", "openai"): "https://dashscope.aliyuncs.com/compatible-mode/v1",
            _key("singapore", "*", "openai"): "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            _key("us", "*", "openai"): "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
            _key("china", "*", "native"): "https://dashscope.aliyuncs.com/api/v1",
            _key("singapore", "*", "native"): "https://dashscope-intl.aliyuncs.com/api/v1",
            _key("us", "*", "native"): "https://dashscope-us.aliyuncs.com/api/v1",
        },
    ),
    "deepseek": _preset(
        "deepseek",
        regions=("international",),
        plans=("payg",),
        protocols=("openai", "anthropic"),
        default_region="international",
        default_plan="payg",
        default_protocol="openai",
        docs_url="https://api-docs.deepseek.com/",
        notes="Bearer API key only. No OAuth. Anthropic Messages uses /anthropic.",
        bases={
            _key("international", "*", "openai"): "https://api.deepseek.com",
            _key("international", "*", "anthropic"): "https://api.deepseek.com/anthropic",
        },
    ),
    "moonshot": _preset(
        "moonshot",
        regions=("china", "international"),
        plans=("payg", "token"),
        protocols=("openai",),
        default_region="international",
        default_plan="payg",
        default_protocol="openai",
        docs_url="https://platform.moonshot.ai/docs",
        notes=(
            "China (api.moonshot.cn) and international (api.moonshot.ai) are "
            "different accounts. Kimi Coding uses the separate Kimi Coding slot."
        ),
        bases={
            _key("china", "*", "openai"): "https://api.moonshot.cn/v1",
            _key("international", "*", "openai"): "https://api.moonshot.ai/v1",
        },
    ),
    "kimi_coding": _preset(
        "kimi_coding",
        regions=("international",),
        plans=("coding",),
        protocols=("anthropic",),
        default_region="international",
        default_plan="coding",
        default_protocol="anthropic",
        docs_url="https://platform.moonshot.ai/docs",
        notes="Kimi Coding Plan key (sk-kimi-*). Not interchangeable with a Kimi API key.",
        bases={
            _key("*", "coding", "anthropic"): "https://api.kimi.com/coding/v1",
        },
    ),
    "minimax": _preset(
        "minimax",
        regions=("china", "international"),
        plans=("payg", "token", "coding"),
        protocols=("openai", "anthropic"),
        default_region="international",
        default_plan="payg",
        default_protocol="openai",
        docs_url="https://platform.minimaxi.com/document/Announcement",
        notes=(
            "China is api.minimaxi.com, international is api.minimax.io. "
            "Pay-as-you-go and subscription keys are not interchangeable."
        ),
        bases={
            _key("china", "*", "openai"): "https://api.minimaxi.com/v1",
            _key("international", "*", "openai"): "https://api.minimax.io/v1",
            _key("china", "*", "anthropic"): "https://api.minimaxi.com/anthropic",
            _key("international", "*", "anthropic"): "https://api.minimax.io/anthropic",
        },
    ),
    "zai": _preset(
        "zai",
        regions=("international",),
        plans=("payg", "token", "coding"),
        protocols=("openai",),
        default_region="international",
        default_plan="payg",
        default_protocol="openai",
        docs_url="https://docs.z.ai/guides/overview/quick-start",
        notes="Z.ai international. General API and Coding Plan use different hosts and keys.",
        bases={
            _key("international", "payg", "openai"): "https://api.z.ai/api/paas/v4",
            _key("international", "token", "openai"): "https://api.z.ai/api/paas/v4",
            _key("international", "coding", "openai"): "https://api.z.ai/api/coding/paas/v4",
        },
    ),
    "zhipu": _preset(
        "zhipu",
        regions=("china",),
        plans=("payg", "token", "coding"),
        protocols=("openai",),
        default_region="china",
        default_plan="payg",
        default_protocol="openai",
        docs_url="https://open.bigmodel.cn/dev/api",
        notes="BigModel China. A Z.ai key is rejected here. Coding Plan has its own endpoint.",
        bases={
            _key("china", "payg", "openai"): "https://open.bigmodel.cn/api/paas/v4",
            _key("china", "token", "openai"): "https://open.bigmodel.cn/api/paas/v4",
            _key("china", "coding", "openai"): "https://open.bigmodel.cn/api/coding/paas/v4",
        },
    ),
    "siliconflow": _preset(
        "siliconflow",
        regions=("china", "international"),
        plans=("payg",),
        protocols=("openai",),
        default_region="china",
        default_plan="payg",
        default_protocol="openai",
        docs_url="https://docs.siliconflow.cn/",
        notes="Aggregator for DeepSeek, Qwen, GLM and Kimi. China and international hosts differ.",
        bases={
            _key("china", "*", "openai"): "https://api.siliconflow.cn/v1",
            _key("international", "*", "openai"): "https://api.siliconflow.com/v1",
        },
    ),
    "volcengine": _preset(
        "volcengine",
        regions=("china", "international"),
        plans=("payg", "token", "coding"),
        protocols=("openai",),
        default_region="china",
        default_plan="payg",
        default_protocol="openai",
        docs_url="https://www.volcengine.com/docs/82379",
        notes="Doubao / Ark in China, BytePlus Ark internationally. Coding Plan uses the same host with a plan key.",
        bases={
            _key("china", "*", "openai"): "https://ark.cn-beijing.volces.com/api/v3",
            _key("international", "*", "openai"): "https://ark.ap-southeast.bytepluses.com/api/v3",
        },
    ),
    "hunyuan": _preset(
        "hunyuan",
        regions=("china", "international"),
        plans=("payg",),
        protocols=("openai",),
        default_region="china",
        default_plan="payg",
        default_protocol="openai",
        docs_url="https://cloud.tencent.com/document/product/1729",
        notes="OpenAI-compatible API key. Enterprise SecretId + SecretKey is a later Tencent Cloud path.",
        bases={
            _key("china", "*", "openai"): "https://api.hunyuan.cloud.tencent.com/v1",
            _key("international", "*", "openai"): "https://api.hunyuan.cloud.tencent.com/v1",
        },
    ),
    "qianfan": _preset(
        "qianfan",
        regions=("china",),
        plans=("payg", "token"),
        protocols=("openai",),
        default_region="china",
        default_plan="payg",
        default_protocol="openai",
        docs_url="https://cloud.baidu.com/doc/qianfan/s/",
        notes="Baidu Qianfan / ERNIE. Bearer API key on the v2 OpenAI-compatible host.",
        bases={
            _key("china", "*", "openai"): "https://qianfan.baidubce.com/v2",
        },
    ),
    "stepfun": _preset(
        "stepfun",
        regions=("china", "international"),
        plans=("payg", "token"),
        protocols=("openai",),
        default_region="china",
        default_plan="payg",
        default_protocol="openai",
        docs_url="https://platform.stepfun.com/docs/overview",
        notes="StepFun LLM and image share this key. Token plan may use a different host.",
        bases={
            _key("china", "*", "openai"): "https://api.stepfun.com/v1",
            _key("international", "*", "openai"): "https://api.stepfun.ai/v1",
        },
    ),
}

# Old config field names still resolve to the same table.
_ALIASES = {
    "dashscope": "qwen",
    "alibaba": "qwen",
}


def connection_preset(provider: str) -> ConnectionPreset | None:
    name = (provider or "").replace("-", "_").strip().lower()
    name = _ALIASES.get(name, name)
    return CONNECTION_PRESETS.get(name)


def lookup_connection_base(
    provider: str,
    region: str | None,
    plan: str | None,
    protocol: str | None,
) -> str | None:
    preset = connection_preset(provider)
    if preset is None:
        return None
    region = (region or preset.default_region).strip().lower()
    plan = (plan or preset.default_plan).strip().lower()
    protocol = (protocol or preset.default_protocol).strip().lower()
    candidates = (
        _key(region, plan, protocol),
        _key(region, "*", protocol),
        _key(region, plan, "*"),
        _key(region, "*", "*"),
        _key("*", plan, protocol),
        _key("*", "*", protocol),
        _key("*", plan, "*"),
        _key("*", "*", "*"),
    )
    for item in candidates:
        found = preset.bases.get(item)
        if found:
            return found
    return None


def resolve_connection_api_base(provider: str, config: Any | None) -> str | None:
    """Resolved base for a saved provider config (explicit api_base wins)."""
    if config is not None:
        explicit = str(getattr(config, "api_base", None) or "").strip()
        if explicit:
            return explicit
    region = getattr(config, "endpoint_region", None) if config is not None else None
    plan = getattr(config, "access_plan", None) if config is not None else None
    protocol = getattr(config, "wire_protocol", None) if config is not None else None
    if isinstance(region, str) and region in {"", "auto"}:
        region = None
    return lookup_connection_base(provider, region, plan, protocol)


def connection_payload(provider: str) -> dict[str, Any] | None:
    preset = connection_preset(provider)
    if preset is None:
        return None
    return {
        "regions": [{"id": item.id, "label": item.label} for item in preset.regions],
        "plans": [{"id": item.id, "label": item.label} for item in preset.plans],
        "protocols": [{"id": item.id, "label": item.label} for item in preset.protocols],
        "default_region": preset.default_region,
        "default_plan": preset.default_plan,
        "default_protocol": preset.default_protocol,
        "bases": dict(preset.bases),
        "docs_url": preset.docs_url or None,
        "notes": preset.notes or None,
    }
