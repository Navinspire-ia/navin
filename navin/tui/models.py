# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared model presentation for the terminal picker and settings."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, OptionList, Select, Static

from navin.providers.model_capabilities import supports_vision
from navin.providers.registry import find_by_model, find_by_name
from navin.tui.screens import _SELECT_BLANK, FormScreen, PickerScreen, PickItem

MODALITY_ORDER = {"text": 0, "image": 1, "video": 2, "audio": 3, "music": 4, "stt": 5}
MODALITY_LABELS = {
    "text": "Chat / Multimodal", "image": "Images", "video": "Video",
    "audio": "Audio", "music": "Music", "stt": "Transcription",
}


def model_kind(model: str, modality: str = "text", inputs: list[str] | None = None) -> str:
    if modality != "text":
        return MODALITY_LABELS.get(modality, modality)
    multimodal = supports_vision(model, input_modalities=inputs) or bool(
        set(inputs or []) & {"audio", "video", "image"}
    )
    return "Chat + Multimodal" if multimodal else "Chat"


def provider_label(provider: str, model: str = "") -> str:
    spec = find_by_name(provider) if provider and provider != "auto" else find_by_model(model)
    return spec.display_name if spec else (provider or "Auto")


def model_pick_items(rows: list[dict[str, Any]]) -> list[PickItem]:
    visible = [row for row in rows if row.get("enabled", True)]
    visible.sort(key=lambda r: (
        MODALITY_ORDER.get(r.get("modality", "text"), 6),
        provider_label(r.get("provider", ""), r.get("model", "")).lower(),
        r["name"] != "default",
        (r.get("label") or r["name"]).lower(),
    ))
    return [PickItem(
        row["name"], row.get("label") or row["name"], row.get("model") or "Not configured",
        model_kind(row.get("model", ""), row.get("modality", "text"), row.get("input_modalities")),
        group=f"{MODALITY_LABELS.get(row.get('modality', 'text'), 'Other')}  /  "
              f"{provider_label(row.get('provider', ''), row.get('model', ''))}",
    ) for row in visible]


def reasoning_options(provider: str, model: str) -> tuple[tuple[str, str], ...]:
    from navin.webui.settings_api import reasoning_effort_values_payload

    return tuple((value, value if value else "Auto") for value in
                 reasoning_effort_values_payload(provider, model)["values"])


class ModelPickerScreen(PickerScreen):
    DEFAULT_CSS = """
    ModelPickerScreen > Vertical { width: 94; max-height: 38; }
    ModelPickerScreen #model-actions { height: 3; margin-top: 1; }
    ModelPickerScreen Button { margin-right: 1; min-width: 16; }
    ModelPickerScreen #options { width: 1fr; min-width: 0; border: none; }
    ModelPickerScreen #options > .option-list--option { padding: 0 1; }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Models", classes="picker-title")
            yield Static("Enter: use chat model / configure media. Esc: close.", classes="picker-hint")
            yield Static("Search  model, provider or capability", id="filter", markup=False)
            yield OptionList(id="options")
            with Horizontal(id="model-actions"):
                yield Button("Configure models", id="configure-models")
                yield Button("Model routing", id="model-routing")

    def _paint_field(self) -> None:
        super()._paint_field()
        if self.is_mounted and not self._query:
            self.query_one("#filter", Static).update("Search  model, provider or capability")

    def _option_line(self, item: PickItem, mark: str) -> Text:
        line = Text(f"{mark} {item.title}  ", no_wrap=True, overflow="ellipsis")
        line.append(f"[{item.badge}]", style="#87AFD7")
        line.append("\n  ")
        line.append(item.subtitle, style="#9A9A9A")
        return line

    def action_confirm(self) -> None:
        if isinstance(self.focused, Button):
            self.focused.press()
        else:
            super().action_confirm()

    @on(Button.Pressed, "#configure-models")
    def configure_models(self) -> None:
        self.dismiss("__settings__:models")

    @on(Button.Pressed, "#model-routing")
    def configure_routing(self) -> None:
        self.dismiss("__settings__:routing")


class ModelFormScreen(FormScreen):
    """Refresh supported effort whenever the provider or model is edited."""

    def on_mount(self) -> None:
        super().on_mount()
        self._refresh_reasoning()

    @on(Input.Changed, "#f-model")
    @on(Select.Changed, "#f-provider")
    def model_changed(self) -> None:
        if self.is_mounted:
            self._refresh_reasoning()

    def _refresh_reasoning(self) -> None:
        fields = {field.name: field for field in self._fields}
        provider = self._value_of(fields["provider"])
        model = self._value_of(fields["model"])
        select = self.query_one("#f-reasoning_effort", Select)
        current = select.value
        options = [(label, value or _SELECT_BLANK) for value, label in reasoning_options(provider, model)]
        select.set_options(options)
        select.value = current if current in [v for _, v in options] else _SELECT_BLANK
        select.tooltip = "Native reasoning effort for this provider and model"
