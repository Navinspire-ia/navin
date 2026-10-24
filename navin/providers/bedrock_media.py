# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""AWS Bedrock for the media uses (listening, voice, Live) with the user's own key.

Each media model lives in one AWS region, not always the one set for chat:
Voxtral in Ireland, Nova 2 Sonic in Stockholm, Polly wherever the account
uses it. The table below is the source of truth for the pickers and the
adapters, so a model always runs in the region that serves it.

Credentials are the Bedrock provider's (Settings > Providers > AWS Bedrock):
a Bedrock API key, or an AWS profile / the standard AWS environment. The
adapters only receive ``api_key`` and ``api_base``; ``api_base`` carries the
region and profile as ``bedrock://<region>[?profile=<name>]``.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator
from urllib.parse import parse_qs, quote, urlsplit

#: ``api_key`` placeholder when the AWS profile / environment holds the credentials.
AWS_CREDENTIALS_MARKER = "aws"
#: Region used when neither the settings, the environment nor the AWS profile
#: name one: Ohio, the only region the inference account may call since AWS
#: blocked the others (quotas used up).
DEFAULT_REGION = "us-east-2"


@dataclass(frozen=True)
class BedrockMediaModel:
    kind: str  # stt | tts | live
    id: str
    label: str
    #: Region that serves the model; None = the region set for the provider.
    region: str | None


BEDROCK_MEDIA_MODELS: tuple[BedrockMediaModel, ...] = (
    BedrockMediaModel("stt", "mistral.voxtral-small-24b-2507", "Voxtral Small", "eu-west-1"),
    BedrockMediaModel("stt", "mistral.voxtral-mini-3b-2507", "Voxtral Mini", "eu-west-1"),
    BedrockMediaModel("tts", "polly-neural", "Polly Neural", None),
    BedrockMediaModel("tts", "polly-generative", "Polly Generative", "eu-central-1"),
    BedrockMediaModel("live", "amazon.nova-2-sonic-v1:0", "Nova 2 Sonic", "eu-north-1"),
)

_BY_ID = {model.id: model for model in BEDROCK_MEDIA_MODELS}


def media_rows(kind: str) -> list[dict[str, Any]]:
    """Picker rows for one use (same shape as the provider model lists)."""
    rows = []
    for model in BEDROCK_MEDIA_MODELS:
        if model.kind != kind:
            continue
        where = f" · {model.region}" if model.region else ""
        rows.append({
            "id": model.id,
            "label": model.label,
            "owned_by": f"AWS Bedrock{where}",
            "media_modalities": [kind],
            "context_window": None,
        })
    return rows


def model_region(model: str, fallback: str | None) -> str:
    known = _BY_ID.get((model or "").strip())
    return (known.region if known and known.region else None) or fallback or DEFAULT_REGION


def encode_target(region: str | None, profile: str | None) -> str:
    base = f"bedrock://{(region or DEFAULT_REGION).strip()}"
    return f"{base}?profile={quote(profile.strip())}" if profile and profile.strip() else base


def decode_target(api_base: str | None) -> tuple[str, str | None]:
    """(region, profile) from an ``api_base`` written by :func:`encode_target`."""
    text = (api_base or "").strip()
    if not text.startswith("bedrock://"):
        return (os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or DEFAULT_REGION), None
    parts = urlsplit(text)
    profile = (parse_qs(parts.query).get("profile") or [None])[0]
    return (parts.netloc or DEFAULT_REGION), (profile or None)


def provider_credentials(provider_cfg: Any) -> tuple[str, str]:
    """(api_key, api_base) for the media adapters, ("", "") when nothing is set.

    A Bedrock API key is used as is; otherwise an AWS profile (or the AWS
    environment, ``AWS_ACCESS_KEY_ID`` / ``AWS_PROFILE``) makes the provider
    usable and ``api_key`` is the :data:`AWS_CREDENTIALS_MARKER`.
    """
    api_key = str(getattr(provider_cfg, "api_key", "") or "").strip() if provider_cfg else ""
    profile = str(getattr(provider_cfg, "profile", "") or "").strip() if provider_cfg else ""
    region = str(getattr(provider_cfg, "region", "") or "").strip() if provider_cfg else ""
    env_ready = bool(os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE")
                     or os.environ.get("AWS_BEARER_TOKEN_BEDROCK"))
    if not (api_key or profile or env_ready):
        return "", ""
    return (api_key or AWS_CREDENTIALS_MARKER), encode_target(region or None, profile or None)


def bedrock_api_key(api_key: str | None) -> str | None:
    """The Bedrock API key itself, or None when the AWS credentials are used."""
    key = (api_key or "").strip()
    return key if key and key != AWS_CREDENTIALS_MARKER else None


@contextmanager
def bearer_token(api_key: str | None) -> Iterator[None]:
    """Expose a Bedrock API key to the AWS SDK for the duration of one call."""
    key = bedrock_api_key(api_key)
    if not key:
        yield
        return
    previous = os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
    os.environ["AWS_BEARER_TOKEN_BEDROCK"] = key
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("AWS_BEARER_TOKEN_BEDROCK", None)
        else:
            os.environ["AWS_BEARER_TOKEN_BEDROCK"] = previous


def aws_session(profile: str | None = None, role_arn: str | None = None) -> Any:
    """boto3 session, optionally assuming a role in the central inference account.

    Workload accounts call Bedrock in one dedicated account (AWS recommendation):
    their own credentials only need sts:AssumeRole on ``role_arn``. The assumed
    credentials refresh themselves before they expire.
    """
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - only without the bedrock feature
        raise RuntimeError("AWS Bedrock support is not installed: save the Bedrock provider to install it.") from exc
    base = boto3.Session(profile_name=profile) if profile else boto3.Session()
    role_arn = (role_arn or os.environ.get("NAVIN_BEDROCK_ROLE_ARN") or "").strip()
    if not role_arn:
        return base
    from botocore.credentials import RefreshableCredentials
    from botocore.session import get_session

    sts = base.client("sts")

    def _refresh() -> dict[str, str]:
        creds = sts.assume_role(RoleArn=role_arn, RoleSessionName="navin-bedrock")["Credentials"]
        return {
            "access_key": creds["AccessKeyId"],
            "secret_key": creds["SecretAccessKey"],
            "token": creds["SessionToken"],
            "expiry_time": creds["Expiration"].isoformat(),
        }

    botocore_session = get_session()
    botocore_session._credentials = RefreshableCredentials.create_from_metadata(
        metadata=_refresh(), refresh_using=_refresh, method="sts-assume-role"
    )
    return boto3.Session(botocore_session=botocore_session)


def boto_client(service: str, region: str, profile: str | None, role_arn: str | None = None) -> Any:
    return aws_session(profile, role_arn).client(service, region_name=region)
