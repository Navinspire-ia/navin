# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Restrained Markdown accents for paths and recognizable shell commands."""

from __future__ import annotations

import re
from typing import Any

from textual.content import Content, Span
from textual.css.model import SelectorType
from textual.css.styles import RenderStyles, Styles
from textual.css.stylesheet import Stylesheet
from textual.dom import DOMNode
from textual.widgets.markdown import MarkdownBlock

from navin.tui.paths import looks_like_path

_INSTALLED = False
_COMMAND = re.compile(
    r"^(?:\$\s+|(?:sudo\s+)?(?:git|npm|npx|pnpm|yarn|bun|uv|pip|pip3|"
    r"python|python3|pytest|ruff|node|cargo|go|make|docker|kubectl|curl|"
    r"ls|cd|rg|ssh|navin)\s+|\./\S+(?:\s|$))"
)


def readable_validation_report(text: str) -> str | None:
    """Render stored pre-report validation messages without rewriting history."""
    reasons = {
        "The changes are saved, but the task is not validated.": "repeated",
        "The changes are saved, but validation is still incomplete.": "no_progress",
        "The task ended before validation was completed.": "ended",
    }
    reason = next((reason for prefix, reason in reasons.items() if text.startswith(prefix)), None)
    if reason is None:
        return None
    from navin.agent.code_validation import CodeValidationState

    _, separator, details = text.partition("\n\n")
    if not separator:
        return None
    body, _, paths = details.partition("Changed files: ")
    state = CodeValidationState(
        revision=1,
        needs_tests=any(phrase in body for phrase in (
            "Run meaningful tests for the requested behavior",
            "Run the tests that cover the changed files",
        )),
        paths={path.strip() for path in paths.strip().split(", ") if path.strip()},
    )
    instructions = (
        "Run meaningful tests for the requested behavior",
        "Run the changed tests, for example with exec:",
        "Run the tests that cover the changed files",
        "whole suite: the full suite is for shared code",
        "Tests covering the changed files",
        "Pick the tests closest to the changed code",
        "Pick the one or two tests closest to the changed code",
        "Wait for the test process to finish, inspect its results",
        "Run an appropriate check after the latest edits.",
    )
    # Preserve diagnostics we cannot classify rather than treating them as proof
    # that tests passed or failed. This adapter only changes the presentation.
    remaining = [line for line in body.splitlines() if line.strip() and not line.startswith(instructions)]
    state.test_result_note = "\n\n".join(remaining)
    return state.completion_message(reason=reason)


# Outcome words carry the result of a turn ("14 tests passed, lint clean,
# verify PASS"). Uppercase verdicts and counted results only: prose such as
# "error handling" stays neutral.
_STATUS_OK = re.compile(
    r"\b(?:PASS(?:ED)?|OK|SUCCESS)\b|[✓✔]"
    r"|\b\d+\s+(?:tests?\s+|checks?\s+)?(?:passed|pass|passing|passés?|passent|réussis?|ok)\b"
    r"|\b(?:lint|verify|build|typecheck)\s+(?:propre|clean|vert|green|ok)\b",
)
_STATUS_FAIL = re.compile(
    r"\b(?:FAIL(?:ED|URE)?|ERROR)\b|[✗✖❌]"
    r"|\b\d+\s+(?:tests?\s+|checks?\s+)?(?:failed|failing|failures?|errors?|échoués?|échecs?|en échec)\b"
)
_STATUS_WARN = re.compile(r"\b(?:WARN(?:ING)?|SKIPPED)\b|⚠|\b\d+\s+(?:skipped|warnings?|ignorés?)\b")


def _status_spans(plain: str) -> list[Span]:
    spans: list[Span] = []
    for pattern, style in ((_STATUS_OK, ".status_ok"), (_STATUS_FAIL, ".status_fail"), (_STATUS_WARN, ".status_warn")):
        spans.extend(Span(match.start(), match.end(), style) for match in pattern.finditer(plain))
    return spans


def restyle_inline_code(content: Content) -> Content:
    """Accent commands, paths and outcome words; identifiers stay neutral."""
    plain = content.plain
    status = _status_spans(plain) if plain else []
    if not content.spans and not status:
        return content
    new_spans: list[Span] = []
    changed = bool(status)
    for span in content.spans:
        if span.style == ".code_inline":
            piece = plain[span.start : span.end]
            if _COMMAND.match(piece.strip()):
                new_spans.append(Span(span.start, span.end, ".code_command"))
                changed = True
                continue
            if looks_like_path(piece):
                new_spans.append(Span(span.start, span.end, ".code_path"))
                changed = True
                continue
        new_spans.append(span)
    if not changed:
        return content
    return Content(plain, spans=new_spans + status)


def install_path_styles() -> None:
    """Patch Textual Markdown so path-like inline code uses ``.code_path``."""
    global _INSTALLED
    if _INSTALLED:
        return
    classes = set(MarkdownBlock.COMPONENT_CLASSES)
    classes.update({"code_path", "code_command", "status_ok", "status_fail", "status_warn"})
    MarkdownBlock.COMPONENT_CLASSES = classes
    original = MarkdownBlock._token_to_content

    def _token_to_content(self: MarkdownBlock, token: object) -> Content:
        return restyle_inline_code(original(self, token))

    MarkdownBlock._token_to_content = _token_to_content  # type: ignore[method-assign]
    install_component_style_cache()
    _INSTALLED = True


# -- component class styles ------------------------------------------------
#
# Textual resolves every component class of a node (9 per Markdown block with
# the accents above) on each restyle by building a virtual DOMNode per class
# and matching the whole stylesheet against it, uncached. A long transcript
# holds thousands of blocks, so one screen restyle spent most of its time
# there. The result only depends on the stylesheet and on the ancestor
# features its relevant selectors can test (type, referenced ids and classes,
# referenced pseudo-classes), so blocks with the same signature share it.

_ORIGINAL_PROCESS_COMPONENT_CLASSES: Any = None
_COMPONENT_STYLE_CACHE_LIMIT = 2048
_COMPONENT_CACHE_ATTR = "_navin_component_style_cache"


class _ComponentStyles(RenderStyles):
    """Component styles sharing cached rules, with Textual's virtual node made on demand.

    Visual styles only read the rules. ``node`` (used by ``rich_style`` and
    animation) is the same virtual node Textual would have attached.
    """

    def __init__(self, owner: DOMNode, component: str, base: Styles, inline: Styles) -> None:
        self._owner = owner
        self._component = component
        self._virtual: DOMNode | None = None
        super().__init__(None, base, inline)  # type: ignore[arg-type]

    @property  # type: ignore[override]
    def node(self) -> DOMNode | None:
        if self._virtual is None and self._owner is not None:
            virtual = DOMNode(classes=self._component)
            virtual._attach(self._owner)
            virtual._css_styles = self._base_styles
            virtual._inline_styles = self._inline_styles
            virtual.styles = self
            self._virtual = virtual
        return self._virtual

    @node.setter
    def node(self, value: DOMNode | None) -> None:
        self._virtual = value


def _component_features(stylesheet: Stylesheet, components: frozenset[str]) -> tuple:
    """Ancestor features the selectors of rules for these virtual nodes can test."""
    names: set[str] = set()
    for component in components:
        names |= DOMNode(classes=component)._selector_names
    rules_map = stylesheet.rules_map
    classes: set[str] = set()
    ids: set[str] = set()
    pseudo: set[str] = set()
    for name in names:
        for rule in rules_map.get(name, ()):
            for selector_set in rule.selector_set:
                for selector in selector_set.selectors:
                    if selector.type == SelectorType.CLASS:
                        classes.add(selector.name)
                    elif selector.type == SelectorType.ID:
                        ids.add(selector.name)
                    pseudo |= selector.pseudo_classes
    return frozenset(classes), frozenset(ids), tuple(sorted(pseudo))


def _component_key(node: DOMNode, features: tuple) -> tuple:
    classes, ids, pseudo = features
    chain = []
    for ancestor in node.ancestors_with_self:
        node_id = ancestor._id
        chain.append((
            type(ancestor),
            node_id if node_id in ids else None,
            frozenset(ancestor._classes & classes) if classes else None,
            tuple(ancestor.has_pseudo_class(name) for name in pseudo) if pseudo else None,
        ))
    return tuple(chain)


def _resolve_component_rules(stylesheet: Stylesheet, node: DOMNode, components: frozenset[str]) -> dict:
    """Textual's resolution: one virtual node per class, matched in place."""
    resolved: dict[str, tuple[Styles, Styles]] = {}
    for component in sorted(components):
        virtual = DOMNode(classes=component)
        virtual._attach(node)
        stylesheet.apply(virtual, animate=False)
        styles = virtual.styles
        resolved[component] = (styles.base, styles.inline)
        # The cache must not keep this block (and its message) alive.
        virtual._detach()
    return resolved


def _process_component_classes(self: Stylesheet, node: DOMNode) -> None:
    original = _ORIGINAL_PROCESS_COMPONENT_CLASSES
    if not isinstance(node, MarkdownBlock):
        return original(self, node)
    components = node._get_component_classes()
    if not components:
        return original(self, node)
    try:
        rules = self.rules
        state = self.__dict__.get(_COMPONENT_CACHE_ATTR)
        if state is None or state[0] is not rules:
            # New parse (theme, variables, added CSS): drop everything.
            state = (rules, {}, {})
            self.__dict__[_COMPONENT_CACHE_ATTR] = state
        _, feature_cache, style_cache = state
        features = feature_cache.get(components)
        if features is None:
            features = feature_cache[components] = _component_features(self, components)
        key = (components, _component_key(node, features))
    except Exception:  # noqa: BLE001 - unknown node or selector shape: Textual's path
        return original(self, node)
    resolved = style_cache.get(key)
    if resolved is None:
        resolved = _resolve_component_rules(self, node, components)
        if len(style_cache) >= _COMPONENT_STYLE_CACHE_LIMIT:
            style_cache.clear()
        style_cache[key] = resolved
    current = node._component_styles
    if len(current) == len(resolved) and all(
        (styles := current.get(component)) is not None
        and styles._base_styles._rules == base._rules
        and styles._inline_styles._rules == inline._rules
        for component, (base, inline) in resolved.items()
    ):
        return None
    current.clear()
    for component, (base, inline) in resolved.items():
        current[component] = _ComponentStyles(node, component, base, inline)
    node.refresh()
    return None


def install_component_style_cache() -> None:
    """Share component-class styles between Markdown blocks with equal signatures."""
    global _ORIGINAL_PROCESS_COMPONENT_CLASSES
    if _ORIGINAL_PROCESS_COMPONENT_CLASSES is not None:
        return
    original = getattr(Stylesheet, "_process_component_classes", None)
    code = getattr(original, "__code__", None)
    # Only replace the Textual implementation this mirrors.
    expected = {"_get_component_classes", "_component_styles", "_attach", "apply"}
    if code is None or not expected <= set(code.co_names) or not hasattr(DOMNode, "_selector_names"):
        return
    _ORIGINAL_PROCESS_COMPONENT_CLASSES = original
    Stylesheet._process_component_classes = _process_component_classes  # type: ignore[method-assign]
