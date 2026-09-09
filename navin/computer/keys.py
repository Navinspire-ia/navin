# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Key names as models write them, normalised once for every backend.

Models mix conventions freely: ``ctrl+c``, ``Control_L``, ``Cmd+Shift+T``,
``Return``, ``enter``, ``PageDown``, ``super``. Every backend receives a
:class:`KeyCombo` whose modifiers are drawn from ``ctrl / shift / alt / meta``
and whose key is one canonical lowercase name from :data:`CANONICAL_KEYS` or a
single printable character.
"""

from __future__ import annotations

from dataclasses import dataclass

MODIFIER_ALIASES: dict[str, str] = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "control_l": "ctrl",
    "control_r": "ctrl",
    "ctl": "ctrl",
    "shift": "shift",
    "shift_l": "shift",
    "shift_r": "shift",
    "alt": "alt",
    "alt_l": "alt",
    "alt_r": "alt",
    "option": "alt",
    "opt": "alt",
    "menu": "alt",
    "meta": "meta",
    "super": "meta",
    "super_l": "meta",
    "super_r": "meta",
    "win": "meta",
    "windows": "meta",
    "cmd": "meta",
    "command": "meta",
    "meta_l": "meta",
    "meta_r": "meta",
}

# Every alias on the left maps to the canonical name on the right.
KEY_ALIASES: dict[str, str] = {
    "enter": "enter",
    "return": "enter",
    "ret": "enter",
    "kp_enter": "enter",
    "esc": "escape",
    "escape": "escape",
    "tab": "tab",
    "space": "space",
    "spacebar": "space",
    " ": "space",
    "backspace": "backspace",
    "back": "backspace",
    "bksp": "backspace",
    "delete": "delete",
    "del": "delete",
    "insert": "insert",
    "ins": "insert",
    "home": "home",
    "end": "end",
    "pageup": "pageup",
    "page_up": "pageup",
    "pgup": "pageup",
    "prior": "pageup",
    "pagedown": "pagedown",
    "page_down": "pagedown",
    "pgdn": "pagedown",
    "next": "pagedown",
    "up": "up",
    "arrowup": "up",
    "down": "down",
    "arrowdown": "down",
    "left": "left",
    "arrowleft": "left",
    "right": "right",
    "arrowright": "right",
    "capslock": "capslock",
    "caps_lock": "capslock",
    "numlock": "numlock",
    "num_lock": "numlock",
    "scrolllock": "scrolllock",
    "scroll_lock": "scrolllock",
    "printscreen": "printscreen",
    "print": "printscreen",
    "prtsc": "printscreen",
    "pause": "pause",
    "break": "pause",
    "contextmenu": "contextmenu",
    "apps": "contextmenu",
    "volumeup": "volumeup",
    "volumedown": "volumedown",
    "volumemute": "volumemute",
    "mute": "volumemute",
    "audioplay": "playpause",
    "playpause": "playpause",
    "mediaplaypause": "playpause",
    "plus": "+",
    "minus": "-",
    "equal": "=",
    "equals": "=",
    "comma": ",",
    "period": ".",
    "dot": ".",
    "slash": "/",
    "backslash": "\\",
    "semicolon": ";",
    "apostrophe": "'",
    "quote": "'",
    "grave": "`",
    "backquote": "`",
    "bracketleft": "[",
    "bracketright": "]",
    "underscore": "_",
    "asterisk": "*",
    "less": "<",
    "greater": ">",
    "question": "?",
    "exclam": "!",
    "at": "@",
    "numbersign": "#",
    "hash": "#",
    "dollar": "$",
    "percent": "%",
    "ampersand": "&",
    "parenleft": "(",
    "parenright": ")",
    "colon": ":",
    "quotedbl": '"',
    "bar": "|",
    "asciitilde": "~",
    "tilde": "~",
    "asciicircum": "^",
    "braceleft": "{",
    "braceright": "}",
}

for _n in range(1, 25):
    KEY_ALIASES[f"f{_n}"] = f"f{_n}"

CANONICAL_KEYS: frozenset[str] = frozenset(KEY_ALIASES.values())


@dataclass(frozen=True, slots=True)
class KeyCombo:
    modifiers: tuple[str, ...]
    key: str  # canonical name or a single printable character

    def label(self) -> str:
        return "+".join([*self.modifiers, self.key])

    @property
    def is_character(self) -> bool:
        return len(self.key) == 1 and self.key not in CANONICAL_KEYS


_MOD_ORDER = ("ctrl", "alt", "shift", "meta")


def parse_combo(text: str) -> KeyCombo:
    """``"Ctrl+Shift+t"`` → ``KeyCombo(("ctrl","shift"), "t")``.

    ``+`` on its own or at the end is the plus key (``ctrl++``). Spaces around
    separators are ignored; ``-`` is not a separator because the key itself may
    be a hyphen and models rarely use it.
    """
    raw = (text or "").strip()
    if not raw:
        raise ValueError("no key given")
    if raw == "+":
        return KeyCombo((), "+")
    # "ctrl++" -> ["ctrl", "+"]
    if raw.endswith("++"):
        parts = raw[:-2].split("+") + ["+"]
    else:
        parts = raw.split("+")
    parts = [p.strip() for p in parts]
    if any(not p for p in parts):
        raise ValueError(f"malformed key combo: {text!r}")

    modifiers: list[str] = []
    keys: list[str] = []
    for part in parts:
        low = part.lower()
        if low in MODIFIER_ALIASES:
            mod = MODIFIER_ALIASES[low]
            if mod not in modifiers:
                modifiers.append(mod)
            continue
        keys.append(_canonical_key(part))
    if len(keys) > 1:
        raise ValueError(f"one key per combo, got {len(keys)} in {text!r}")
    if keys:
        key = keys[0]
    elif modifiers:
        # Only modifiers: the last one is tapped ("super" opens the launcher,
        # "ctrl+shift" is meaningless and becomes shift with ctrl held).
        key = modifiers.pop()
    else:
        raise ValueError(f"malformed key combo: {text!r}")
    ordered = tuple(m for m in _MOD_ORDER if m in modifiers)
    return KeyCombo(ordered, key)


def _canonical_key(part: str) -> str:
    low = part.lower()
    if low in KEY_ALIASES:
        return KEY_ALIASES[low]
    if len(part) == 1:
        return part
    # "KP_1" style numpad names, "Digit1", "KeyA" (DOM code names).
    if low.startswith("kp_") and len(low) == 4 and low[3].isdigit():
        return low[3]
    if low.startswith("digit") and len(low) == 6 and low[5].isdigit():
        return low[5]
    if low.startswith("key") and len(low) == 4 and low[3].isalpha():
        return low[3]
    if low.startswith("numpad") and len(low) == 7 and low[6].isdigit():
        return low[6]
    raise ValueError(f"unknown key: {part!r}")
