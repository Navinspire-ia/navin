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
# What the deep pass may give back, counted here instead of the frozen objects:
# in CPython ``gc.get_freeze_count()`` walks the whole frozen set and
# ``gc.get_objects()`` lists every object of a generation. On the long CLI
# sessions of 2026-11-01/02 those counts alone froze the screen up to 3.2 s on
# each session switch (49 times) and 21.7 s right after one deep pass.
_freezes = 0  # gc.freeze() calls made here since start
_teardowns = 0  # loaded chats torn down (``thaw_heap``) since start
# Resident memory read at the last freeze (None: no reading on this system).
_memory_at_freeze: int | None = None


def resident_bytes() -> int | None:
    """This process's resident memory, read in O(1) whatever the heap size."""
    from navin.webui.stall_watch import resident_memory_bytes

    return resident_memory_bytes()


def _freeze(memory: int | None) -> None:
    """``gc.freeze()`` (an O(1) list splice); *memory* is this pass's reading."""
    global _freezes, _memory_at_freeze
    gc.freeze()
    _freezes += 1
    _memory_at_freeze = memory


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


# Gen-1 collections between two ordinary passes, which run at a short quiet
# moment and never walk the frozen heap.
FULL_GC_DUE = 50
# Past this many, the pass runs at the next tick even while the user types.
FULL_GC_FORCED = FULL_GC_DUE * 8
FULL_GC_QUIET_INPUT_S = 2.0
FULL_GC_QUIET_EVENTS_S = 0.5
# Generation a quiet pass collects: every one that is not frozen. Freezing the
# survivors of every pass (it collected the young generations only) also froze
# the chat's widgets before they left it, and a frozen cycle is never
# collected: the long CLIs of 2026-11-02 reached 2.1 to 11.6 GB, and in a probe
# of the real app half of the frozen set was garbage. A quiet pass now
# collects the unfrozen part of the heap and freezes only once it got slow, so
# the next ones stay short.
ORDINARY_GC_GENERATION = 2
FREEZE_AFTER_PASS_S = 0.08
# A pass forced while the user is there (long overdue: a long turn) collects
# the young generations only, a few milliseconds. It freezes once resident
# memory grew this much since the last freeze, so the first quiet pass after a
# long turn does not walk all of it (1.6 s on average, up to 3 s, when nothing
# bounded it: CLI freeze log of 2026-10-27).
FORCED_GC_GENERATION = 1
FREEZE_AFTER_GROWTH_BYTES = 32 * 2**20
# The deep pass (unfreeze, collect everything, freeze) is the one walk of the
# whole heap and it cannot be split: 3 to 52 s in the CLI freeze log of
# 2026-10-30/31 (31 passes, 214 s of frozen screen), up to 75 s on 2026-11-01,
# the long ones on a heap the system had partly paged out while the CLI
# waited. It runs only when it has something to give back AND the user is
# really away: no key, paste, click or wheel for ten minutes, or three with
# the terminal window in the background. One minute was a reading pause, and
# an unfocused terminal on its own was a look at the desktop app: the user came
# back to a screen frozen for the rest of the pass. A timer alone never starts
# it: an idle CLI paid 3 s every 20 minutes to reclaim nothing (27 deep passes
# in one day of the log).
DEEP_GC_QUIET_INPUT_S = 600.0
DEEP_GC_UNFOCUSED_INPUT_S = 180.0
DEEP_GC_QUIET_EVENTS_S = 10.0
# Something to give back: a freeze since the last deep pass (what it froze may
# have left the chat since) and resident memory grown this much past it, half
# as much once a chat was torn down (its frozen widgets are garbage now).
# Memory is read in O(1); counting the frozen objects was a heap walk.
DEEP_GC_GROWTH_BYTES = 256 * 2**20
# Without a memory reading: this many freezes since the last deep pass.
DEEP_GC_FREEZES = 40
# After a deep pass this long the next one waits for an absence this many
# times longer: the user who came back to a frozen screen once is not made to
# wait again after a short break.
DEEP_GC_SLOW_S = 5.0
DEEP_GC_SLOW_ABSENCE = 3
# A pass at least this long is written to the freeze log with its kind, so the
# next audit knows which pass froze the screen.
SLOW_GC_PASS_S = 0.5
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
    """Run the collections ``terminal_gc_policy`` holds back, when nobody waits.

    The quiet pass (every unfrozen generation, a freeze only once it got slow)
    runs at a short quiet moment: no key, paste or click for a couple of
    seconds and no engine event for half a second. The deep pass that walks
    the whole heap waits for the user to be away and for any work of this CLI
    (a turn, a subagent, a project in the background) to be over. No pass
    counts or lists objects: the decisions read the resident memory.
    """

    def __init__(self, clock: Any = time.monotonic, before_collect: Any = None,
                 busy: Any = None, focused: Any = None) -> None:
        self._clock = clock
        self._before_collect = before_collect
        # Work is running: the engine shares this loop, and the 2 s "quiet"
        # gaps are the wait for the model's next token, not a free moment.
        self._busy = busy
        # Whether the terminal window has the focus (terminal focus reports).
        self._focused = focused
        # The last failure of each hook, logged once (the tick runs 8 times a second).
        self._busy_error: str | None = None
        self._release_error: str | None = None
        self.input_at = 0.0
        self.event_at = 0.0
        self.collections = 0
        self.deep_collections = 0
        self.deep_at = self._clock()
        self.last_pass_s = 0.0
        self.last_deep_s = 0.0
        self.memory_after_deep = self._read_memory()
        self._freezes_at_deep = _freezes
        self._teardowns_at_deep = _teardowns
        # gc.get_count()[2] keeps counting the gen-1 collections between two
        # full passes (a forced pass never resets it): due counts from here.
        self._pending_base = 0

    def note_input(self) -> None:
        self.input_at = self._clock()

    def note_event(self) -> None:
        self.event_at = self._clock()

    def tick(self) -> bool:
        """Collect if a pass is due and the moment allows it."""
        if gc.get_threshold()[2] < _NO_AUTOMATIC_FULL_GC or not gc.isenabled():
            return False  # Python still runs full passes on its own
        now = self._clock()
        idle_input = now - self.input_at
        idle_events = now - self.event_at
        quiet = idle_input >= FULL_GC_QUIET_INPUT_S and idle_events >= FULL_GC_QUIET_EVENTS_S
        if quiet and self._is_busy():
            quiet = False  # a turn's 2 s gaps wait for the model's next token
        if quiet and self._deep_due() and self._away(idle_input, idle_events):
            # The one long pass: it gives back what left the chat after a freeze.
            self._deep_pass(now)
            return True
        pending = gc.get_count()[2] - self._pending_base
        if pending < 0:
            # A full collection (here or elsewhere) reset Python's count.
            self._pending_base = 0
            pending = gc.get_count()[2]
        if pending < FULL_GC_DUE:
            return False
        if not quiet and pending < FULL_GC_FORCED:
            return False
        self._collect(forced=not quiet)
        self.collections += 1
        self._pending_base = gc.get_count()[2]
        return True

    def _read_memory(self) -> int | None:
        return resident_bytes()

    def _deep_due(self) -> bool:
        """Whether the deep pass has something to give back."""
        frozen_since = _freezes - self._freezes_at_deep
        if frozen_since <= 0:
            return False  # nothing frozen since: the quiet passes collect the rest
        memory = self._read_memory()
        if memory is None or self.memory_after_deep is None:
            return frozen_since >= DEEP_GC_FREEZES
        needed = DEEP_GC_GROWTH_BYTES
        if _teardowns != self._teardowns_at_deep:
            needed //= 2
        return memory - self.memory_after_deep >= needed

    def _away(self, idle_input: float, idle_events: float) -> bool:
        if idle_events < DEEP_GC_QUIET_EVENTS_S:
            return False
        longer = DEEP_GC_SLOW_ABSENCE if self.last_deep_s >= DEEP_GC_SLOW_S else 1
        if idle_input >= DEEP_GC_QUIET_INPUT_S * longer:
            return True
        return idle_input >= DEEP_GC_UNFOCUSED_INPUT_S * longer and not self._is_focused()

    def _is_busy(self) -> bool:
        if self._busy is None:
            return False
        try:
            busy = bool(self._busy())
        except Exception as exc:
            # A broken hook: no pass now rather than one in the middle of a
            # turn (the forced pass still bounds memory), and the error is
            # logged. Raised from the spinner tick, it would close the CLI.
            text = f"{type(exc).__name__}: {exc}"
            if text != self._busy_error:
                self._busy_error = text
                from loguru import logger

                logger.opt(exception=exc).warning("CLI busy check failed; garbage collection waits for it")
            return True
        self._busy_error = None
        return busy

    def _is_focused(self) -> bool:
        # Textual's App.app_focus: a plain attribute, it cannot fail. A hook
        # that raises is a bug and surfaces instead of being taken as "looking".
        return True if self._focused is None else bool(self._focused())

    def _release(self) -> None:
        if self._before_collect is None:
            return
        try:
            self._before_collect()
        except Exception as exc:
            # The collection still runs (memory must not grow for it), and the
            # failed release is logged. Raised from the spinner tick, it would
            # close the CLI.
            text = f"{type(exc).__name__}: {exc}"
            if text != self._release_error:
                self._release_error = text
                from loguru import logger

                logger.opt(exception=exc).warning("CLI could not release off-screen lines before collecting")
            return
        self._release_error = None

    def _collect(self, *, forced: bool = False) -> None:
        self._release()
        started = time.perf_counter()
        gc.collect(FORCED_GC_GENERATION if forced else ORDINARY_GC_GENERATION)
        took = time.perf_counter() - started
        memory = self._read_memory()
        if forced:
            # Long turn: freeze once memory grew enough that the first quiet
            # pass after it would walk a lot (always, without a reading).
            freeze = memory is None or _memory_at_freeze is None or (
                memory - _memory_at_freeze >= FREEZE_AFTER_GROWTH_BYTES
            )
        else:
            # What this pass kept is long-lived: frozen once walking it got
            # slow, so the next passes cost only the new objects again.
            freeze = took >= FREEZE_AFTER_PASS_S
        if freeze:
            _freeze(memory)  # an O(1) splice: the collection was the cost
        self.last_pass_s = took
        self._log_slow("forced" if forced else "ordinary", memory)

    def _deep_pass(self, now: float) -> None:
        self._release()
        started = time.perf_counter()
        gc.unfreeze()
        gc.collect(2)
        memory = self._read_memory()
        _freeze(memory)
        self.last_pass_s = self.last_deep_s = time.perf_counter() - started
        self.deep_at = now
        self.deep_collections += 1
        self.memory_after_deep = memory
        self._freezes_at_deep = _freezes
        self._teardowns_at_deep = _teardowns
        self._pending_base = gc.get_count()[2]
        self._log_slow("deep", memory)

    def _log_slow(self, kind: str, memory: int | None) -> None:
        if self.last_pass_s < SLOW_GC_PASS_S:
            return
        from loguru import logger

        resident = "unknown" if memory is None else f"{memory / 2**20:.0f} MB"
        logger.warning(
            "CLI {} garbage collection took {:.1f}s (resident memory {})", kind, self.last_pass_s, resident,
        )


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
    """Keep what is alive now (engine, loaded chat) out of the next passes.

    A collection walks every tracked object it is given: with a long chat
    loaded it froze the UI for over half a second. Collect every unfrozen
    generation (the quiet passes keep that part small), so the chat that was
    just torn down is given back instead of frozen with the new one, then
    move the survivors to the permanent generation (an O(1) splice: nothing
    is counted).
    """
    with _gc_lock:
        if not gc.isenabled():
            return
        gc.collect(2)
        _freeze(resident_bytes())


def thaw_heap() -> None:
    """Note that the loaded chat is being torn down (session switch, clear, reload).

    It used to ``gc.unfreeze()`` the heap here. That gave nothing back: the
    history paint that follows collects the young generations only, so the
    old widgets went straight back to the frozen set, and that freeze then
    counted the whole heap (49 times on 2026-11-01/02, up to 3.2 s each). The
    torn-down widgets that were frozen are garbage for the deep pass, which
    now needs half the growth to run.
    """
    global _teardowns
    with _gc_lock:
        _teardowns += 1


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
