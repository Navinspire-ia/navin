# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Paint input changes without waiting for a transcript layout frame."""

import gc
import inspect
import time
from contextlib import contextmanager
from functools import lru_cache
from threading import RLock
from typing import Any

from rich.style import Style
from textual._compositor import ChopsUpdate, LayoutUpdate
from textual.app import ScreenStackError, UnknownModeError
from textual.geometry import NULL_SIZE, Region
from textual.strip import Strip
from textual.widget import Widget, _RenderCache

_gc_lock = RLock()
_gc_users = 0
_gc_original: tuple[int, int, int] | None = None
_gc_applied: tuple[int, int, int] | None = None
# ``gc.get_freeze_count()`` walks the whole permanent generation: tens of ms
# per call on a long session. Read it only after this module freezes or thaws,
# and keep the value here for the 8 Hz idle tick.
_frozen_count: int | None = None


def _note_frozen() -> int:
    """Count the frozen objects once, right after a freeze or a thaw."""
    global _frozen_count
    _frozen_count = gc.get_freeze_count()
    return _frozen_count


def frozen_count() -> int:
    """Frozen objects as of the last freeze or thaw done here (no heap walk)."""
    return _note_frozen() if _frozen_count is None else _frozen_count


@lru_cache(maxsize=64)
def _blank(width: int, style: Style) -> Strip:
    return Strip.blank(width, style)


def background_lines(widget: Widget, crop: Region) -> list[Strip]:
    """Paint an undecorated opaque screen without allocating a full canvas."""
    style = widget.visual_style
    strip = _blank(crop.width, style.rich_style)
    for line_filter in widget.get_line_filters():
        strip = strip.apply_filter(line_filter, style.background)
    return [strip] * crop.height


# Gen-1 collections between two full ones: Python's own cadence under this
# policy. A full pass is due after this many and waits for a quiet moment.
FULL_GC_DUE = 50
# Past this many, the pass runs at the next tick even while the user types,
# so memory stays bounded when the session never goes quiet.
FULL_GC_FORCED = FULL_GC_DUE * 8
FULL_GC_QUIET_INPUT_S = 2.0
FULL_GC_QUIET_EVENTS_S = 0.5
# Survivors of a full pass are frozen: later passes walk only what is new,
# milliseconds instead of ~2 s on a long session. A deep pass (unfreeze,
# collect, freeze) reclaims cycles among frozen objects at most this often,
# and only once the user has been away from the keyboard for a while.
DEEP_GC_INTERVAL_S = 20 * 60.0
DEEP_GC_QUIET_INPUT_S = 60.0
DEEP_GC_QUIET_EVENTS_S = 10.0
# Freezing also keeps widgets that later leave the chat window (a frozen cycle
# is never collected: +240 MB after 30 turns when this was tried once without
# a bound). When the frozen set grows this much past the last deep pass, the
# deep pass runs at the next quiet moment, away from the keyboard or not.
DEEP_GC_GROWTH = 500_000
# Automatic full collections never start on their own while the policy holds:
# ``IdleCollector`` runs them.
_NO_AUTOMATIC_FULL_GC = 1_000_000_000


@contextmanager
def terminal_gc_policy():
    """Reduce full-heap scans caused by short-lived Textual render caches.

    Keep automatic collection enabled and restore the caller's settings on
    exit. Larger young generations let temporary strips expire before they
    promote. Full collections are left to ``IdleCollector``: one walks every
    tracked object (a second on a long session) and used to start at random,
    in the middle of a stream or a keystroke.
    """
    global _gc_users, _gc_original, _gc_applied
    with _gc_lock:
        if _gc_users == 0 and gc.isenabled():
            _gc_original = gc.get_threshold()
            _gc_applied = tuple(max(value, floor) for value, floor in
                                zip(_gc_original, (4000, 10, _NO_AUTOMATIC_FULL_GC)))
            gc.set_threshold(*_gc_applied)
        _gc_users += 1
    try:
        yield
    finally:
        with _gc_lock:
            _gc_users -= 1
            if _gc_users == 0:
                if _gc_original is not None and gc.get_threshold() == _gc_applied:
                    gc.set_threshold(*_gc_original)
                _gc_original = _gc_applied = None


class IdleCollector:
    """Run the full collections ``terminal_gc_policy`` holds back, when quiet.

    Quiet means no key, paste or click for a couple of seconds and no engine
    event for half a second: typically while the model thinks. Survivors are
    frozen after each pass, so the next ones only walk new objects; a deep
    pass that reclaims frozen cycles waits for the user to be away.
    """

    def __init__(self, clock: Any = time.monotonic, before_collect: Any = None,
                 busy: Any = None) -> None:
        self._clock = clock
        self._before_collect = before_collect
        # A turn is running: the engine shares this loop, and the 2 s "quiet"
        # gaps are the wait for the model's next token, not a free moment.
        self._busy = busy
        self.input_at = 0.0
        self.event_at = 0.0
        self.collections = 0
        self.deep_collections = 0
        self.deep_at = self._clock()
        self.frozen_after_deep = _note_frozen()

    def note_input(self) -> None:
        self.input_at = self._clock()

    def note_event(self) -> None:
        self.event_at = self._clock()

    def tick(self) -> bool:
        """Collect if a full pass is due and the moment allows it."""
        if gc.get_threshold()[2] < _NO_AUTOMATIC_FULL_GC or not gc.isenabled():
            return False  # Python still runs full passes on its own
        now = self._clock()
        quiet = (now - self.input_at >= FULL_GC_QUIET_INPUT_S
                 and now - self.event_at >= FULL_GC_QUIET_EVENTS_S)
        frozen = frozen_count()
        away = (now - self.input_at >= DEEP_GC_QUIET_INPUT_S
                and now - self.event_at >= DEEP_GC_QUIET_EVENTS_S)
        overgrown = frozen - self.frozen_after_deep >= DEEP_GC_GROWTH
        if self._is_busy():
            quiet = away = False
        if frozen and ((away and now - self.deep_at >= DEEP_GC_INTERVAL_S) or (quiet and overgrown)):
            # The one long pass: it gives back what left the chat since.
            gc.unfreeze()
            self._collect()
            self.deep_at = now
            self.frozen_after_deep = frozen_count()
            self.deep_collections += 1
            return True
        pending = gc.get_count()[2]
        if pending < FULL_GC_DUE:
            return False
        if not quiet and pending < FULL_GC_FORCED:
            return False
        self._collect()
        self.collections += 1
        return True

    def _is_busy(self) -> bool:
        if self._busy is None:
            return False
        try:
            return bool(self._busy())
        except Exception:  # noqa: BLE001 - unknown state: behave as before
            return False

    def _collect(self) -> None:
        if self._before_collect is not None:
            try:
                self._before_collect()
            except Exception:  # noqa: BLE001 - never skip the collection for it
                pass
        gc.collect(2)
        # Everything still alive now is long-lived (the chat, the widgets, the
        # index): out of the next passes, which then cost only the new objects.
        gc.freeze()
        _note_frozen()


def release_offscreen_lines(root: Widget) -> int:
    """Drop the painted lines of widgets out of view; return how many.

    Every painted line is a Strip carrying seven small caches, and each widget
    keeps the lines it last painted. On a long turn those lines were most of
    the 1.6 M objects a full collection walks. A widget out of view paints
    again from its content when it comes back, as after any refresh. Widgets
    with their own ``render_line`` keep their caches.
    """
    try:
        visible = root.screen._compositor.visible_widgets
    except Exception:  # noqa: BLE001 - not on a screen
        return 0
    released = 0
    for widget in root.walk_children(with_self=False):
        if widget in visible or type(widget).render_line is not Widget.render_line:
            continue
        if not widget._render_cache.lines:
            continue
        widget._styles_cache.clear()
        widget._render_cache = _RenderCache(NULL_SIZE, [])
        # Dirty: the next render_line renders the content again.
        widget._dirty_regions.add(widget.outer_size.region)
        released += 1
    return released


def freeze_heap() -> None:
    """Keep what is alive now (engine, loaded chat) out of full GC scans.

    A full collection walks every tracked object: with a long chat loaded it
    froze the UI for over half a second. Collect the young generations, then
    move the survivors to the permanent generation. ``thaw_heap`` gives them
    back to the collector when the transcript is torn down.
    """
    with _gc_lock:
        if not gc.isenabled():
            return
        gc.collect(1)
        gc.freeze()
        _note_frozen()


def thaw_heap() -> None:
    """Return frozen objects to the collector (session switch, clear, reload)."""
    global _frozen_count
    with _gc_lock:
        gc.unfreeze()  # a no-op when nothing is frozen; no count walk needed
        _frozen_count = 0


_APP_FOCUS_PSEUDO = frozenset({"focus", "blur"})
_APP_FOCUS_NAMES = frozenset({"update_node_styles", "_last_focused_on_app_blur", "set_focus"})


@lru_cache(maxsize=32)
def _app_focus_shape_matches(screen_type: type) -> bool:
    from textual.app import App

    original = getattr(App, "_watch_app_focus", None)
    code = getattr(original, "__code__", None)
    if code is None or not _APP_FOCUS_NAMES <= set(code.co_names):
        return False
    try:
        return "from_app_focus" in inspect.signature(screen_type.set_focus).parameters
    except (TypeError, ValueError, AttributeError):
        return False


def _app_focus_supported(app: Any) -> bool:
    """True when Textual's ``_watch_app_focus`` still has the shape we replace."""
    try:
        screen_type = type(app.screen)
    except Exception:  # noqa: BLE001 - no screen yet: use Textual's own handler
        return False
    return hasattr(app, "_last_focused_on_app_blur") and _app_focus_shape_matches(screen_type)


def _css_type_loaded(name: str) -> bool:
    """Whether any loaded DOM node class carries this CSS type name."""
    from textual.dom import DOMNode

    pending = [DOMNode]
    seen: set[type] = set()
    while pending:
        cls = pending.pop()
        if cls in seen:
            continue
        seen.add(cls)
        if cls.__name__ == name:
            return True
        pending.extend(cls.__subclasses__())
    return False


def _app_focus_rule_targets(app: Any) -> frozenset[str] | None:
    """Rightmost type selectors of rules on the App's :focus / :blur state.

    For example ``HeaderTitle`` for Textual's ``App:blur HeaderTitle``.
    ``None`` means such a rule targets something else (a class, an id, ``*``)
    and only a full restyle is exact. Rules on the App node itself are left
    out: Textual's handler restyles the screen, never the App node. Cached per
    parsed rule list (themes reparse it).
    """
    from textual.css.model import SelectorType

    rules = app.stylesheet.rules
    cached = app.__dict__.get("_navin_app_focus_rules")
    if cached is not None and cached[0] is rules:
        return cached[1]
    app_names = type(app)._css_type_names
    may_match_app = {SelectorType.UNIVERSAL, SelectorType.NESTED}
    types: set[str] | None = set()
    for rule in rules:
        for selector_set in rule.selector_set:
            selectors = selector_set.selectors
            last = selectors[-1]
            for selector in selectors:
                if not (_APP_FOCUS_PSEUDO & selector.pseudo_classes):
                    continue
                if selector.type not in may_match_app and not (
                    selector.type == SelectorType.TYPE and selector.name in app_names
                ):
                    continue
                if selector is last:
                    continue
                if last.type == SelectorType.TYPE and types is not None:
                    types.add(last.name)
                else:
                    types = None
    result = None if types is None else frozenset(types)
    app.__dict__["_navin_app_focus_rules"] = (rules, result)
    return result


def watch_app_focus_without_restyle(app: Any, focus: bool) -> bool:
    """Textual's app focus handler without its screen-wide restyle.

    Textual 6 restyles every node of the screen on each terminal FocusIn or
    FocusOut (seconds with a long transcript). Only rules that use the App's
    ``:focus`` / ``:blur`` need it; widget focus itself goes through
    ``set_focus``, which restyles the widgets whose focus changed. Returns
    False when the caller must use Textual's own handler: unknown Textual
    internals, or a rule on the App focus that matches a widget type in use.
    """
    if not _app_focus_supported(app):
        return False
    try:
        types = _app_focus_rule_targets(app)
    except Exception:  # noqa: BLE001 - unexpected rule model: keep Textual's behaviour
        return False
    if types is None or any(_css_type_loaded(name) for name in types):
        return False
    from textual.dom import NoScreen

    screen = app.screen
    if focus:
        try:
            last = app._last_focused_on_app_blur
            if last is not None and last.screen is screen and screen.focused is None:
                screen.set_focus(last, scroll_visible=False, from_app_focus=True)
        except NoScreen:
            pass
        app._last_focused_on_app_blur = None
    else:
        app._last_focused_on_app_blur = screen.focused
        screen.set_focus(None)
    return True


def paint_input(widget: Widget) -> bool:
    """Use the current composition, including clipping, overlays and scrollbars.

    Layout and other pending repaints still run normally on the next frame.
    Textual's compositor has no regional refresh entry point, so temporarily
    give it just the input region and restore its pending work synchronously.
    """
    if not widget.is_attached or not widget.has_focus:
        return False
    app = widget.app
    screen = widget.screen
    if app._batch_count or app.is_inline or screen is not app.screen:
        return False
    compositor = screen._compositor
    geometry = compositor.visible_widgets.get(widget)
    if geometry is None:
        return False
    region, clip = geometry
    region = region.intersection(clip)
    if not region:
        return False
    pending = compositor._dirty_regions
    compositor._dirty_regions = {region}
    try:
        frame = compositor.render_update(screen_stack=app._background_screens)
    finally:
        pending.update(compositor._dirty_regions)
        compositor._dirty_regions = pending
    if frame is None:
        return False
    app._display(screen, frame)
    return True


def keep_batched_frame(app: Any, screen: Any, renderable: Any) -> None:
    """Remember the rows a frame composed inside ``batch_update()`` was to paint.

    Textual drops a frame composed while a batch is open (``App._display``
    returns early), and the compositor has already forgotten the regions the
    frame covered. A direct ``Screen._refresh_layout()`` inside a batch (the
    chat window keeping the reading position while blocks leave or come back,
    a history page landing) therefore left the terminal with the old rows: the
    same lines shown twice, for as long as nothing else repainted them.
    """
    if renderable is None or not app._batch_count:
        return
    if isinstance(renderable, LayoutUpdate):
        regions = [renderable.region]
    elif isinstance(renderable, ChopsUpdate):
        regions = [Region(x1, y, x2 - x1, 1) for y, x1, x2 in renderable.spans if x2 > x1]
    else:
        return
    lost = getattr(app, "_navin_batched_regions", None)
    if lost is None:
        lost = app._navin_batched_regions = {}
    lost.setdefault(screen, set()).update(regions)


def repaint_batched_frames(app: Any) -> None:
    """Once the last batch closes, paint what the frames dropped inside it covered."""
    if app._batch_count:
        return
    lost = getattr(app, "_navin_batched_regions", None)
    if not lost:
        return
    app._navin_batched_regions = {}
    try:
        current = app.screen
    except (ScreenStackError, UnknownModeError):
        return  # no screen left: shutting down
    for screen, regions in lost.items():
        screen._compositor._dirty_regions.update(regions)
        if screen is not current:
            continue  # painted with the rest when it is on top again
        if (screen._layout_required or screen._scroll_required
                or screen._repaint_required or screen._dirty_widgets):
            # The update already queued paints these rows with the rest.
            screen.check_idle()
        else:
            screen._compositor_refresh()
