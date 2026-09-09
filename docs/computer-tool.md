# Computer Tool (desktop control)

Let the agent operate the real desktop the way a person does: it looks at a screenshot, moves the mouse, clicks, types, presses shortcuts, scrolls, drags, switches windows - in any application, not only the browser. Windows, macOS, Linux X11 and Linux Wayland are supported.

The `browser` tool stays the right choice for web pages (faster, DOM refs, no risk to the desktop). Reach for `computer` when the target is a native window, a system dialog, a file picker, an installer, a legacy app, or a site that only works in the user's own browser profile.

Off by default. Turning it on is a deliberate decision: the agent gets the same reach as the person in front of the machine.

## Enable

```bash
navin computer enable                     # shared desktop, asks before destructive actions
navin computer enable --ask always        # confirm every click and keystroke
navin computer enable --model claude-cu   # route desktop turns to a grounding model preset
navin computer doctor                     # permissions and dependencies, with the fix for each
navin computer status                     # backend, policies, kill switch, model route
```

`pip install "navin-ai[computer]"` adds Pillow (screenshot scaling) and, on Linux, python-xlib. Windows and macOS need nothing else.

Restart the gateway or CLI session after enabling: tools are registered at start.

### Per platform

| Platform | Backend | Needs |
| --- | --- | --- |
| Windows | Win32 `SendInput` + GDI capture, UI Automation snapshot (PowerShell) | Nothing to install. HiDPI is handled per monitor. |
| macOS | Quartz / CoreGraphics via ctypes, `screencapture`, JXA for windows and accessibility | System Settings > Privacy & Security: **Screen Recording** and **Accessibility** for the process that runs Navin (Terminal, the Navin app). `doctor` says which one is missing. |
| Linux X11 (also WSLg, Xvfb) | XTest via python-xlib, `xdotool` fallback, AT-SPI snapshot | `python-xlib` (in the `computer` extra) or `xdotool`. Optional: `python3-gi` + `gir1.2-atspi-2.0` for accessibility refs. |
| Linux Wayland (GNOME, KDE, sway, Hyprland) | `xdg-desktop-portal` RemoteDesktop + ScreenCast over D-Bus / PipeWire; `ydotool`, `wtype`, `grim`, `spectacle` fallbacks | `python3-gi`, `xdg-desktop-portal` with the compositor's backend, `gstreamer1.0-pipewire`. The first run shows the portal consent dialog; the restore token is kept so later runs are silent. |

## Configuration

```yaml
tools:
  computer:
    enabled: false          # default: false
    ask: destructive        # never | destructive | always
    backend: auto           # auto | windows | macos | x11 | wayland
    display: null           # X11 display to drive (":99" for a dedicated Xvfb)
    screenshot_max_width: 1366   # screenshots are scaled into this box before the model sees them
    screenshot_max_height: 768
    settle_ms: 350          # wait after an action before the next screenshot
    type_delay_ms: 8        # per character when typing
    max_actions_per_turn: 60
    user_takeover_px: 40    # a mouse move larger than this by someone else pauses the agent
    failsafe_corner: true   # cursor parked in the top-left corner stops the run
    live_view: true         # stream the desktop into the WebUI panel
    live_view_max_width: 1024
    live_view_quality: 60
    audit_log: true         # JSONL trail of every action under the artifacts folder
    audit_screenshots: true
    protected_apps: ["1Password", "Bitwarden", "KeePass*", ...]   # never looked at nor touched
    blocked_apps: []        # visible but never clicked or typed into
    allowed_apps: []        # when set, actions only inside these apps
    ask_apps: []            # every action inside these apps is confirmed first
    session_mode: shared    # shared (the user's desktop) | dedicated (a display reserved for the agent)
    anthropic_native: false # send the tool as Claude's own computer-use primitive (direct Anthropic provider)
    anthropic_tool_type: computer_20250124
    anthropic_beta: computer-use-2025-01-24
```

Application patterns match the active window title or app name, case-insensitively, with `*` and `?` wildcards.

## How the agent works

One loop, always the same: `screenshot` -> decide -> one action -> read the screenshot that comes back -> verify. Every mutating action returns a fresh screenshot; coordinates are pixels of the screenshot the model was shown and the session remaps them to physical pixels (HiDPI, multi-monitor).

```text
computer(action="screenshot")
computer(action="left_click", coordinate=[412, 288])
computer(action="type", text="Quarterly report")
computer(action="key", text="ctrl+s")
computer(action="scroll", coordinate=[600, 400], scroll_direction="down", scroll_amount=5)
computer(action="left_click_drag", start_coordinate=[100, 100], coordinate=[400, 300])
computer(action="zoom", region=[380, 260, 520, 320])          # read small text
computer(action="snapshot")                                   # accessibility tree with refs
computer(action="left_click", ref=12)                          # click an element by ref, not by pixel
computer(action="windows") / computer(action="focus_window", window="Calculator")
computer(action="wait", duration=1.5)
computer(action="status", doctor=true)
computer(action="close")
```

Actions: `screenshot`, `left_click`, `right_click`, `middle_click`, `double_click`, `triple_click`, `mouse_move`, `left_click_drag`, `left_mouse_down`, `left_mouse_up`, `scroll`, `type`, `key`, `hold_key`, `wait`, `cursor_position`, `zoom`, `windows`, `focus_window`, `snapshot`, `screen_info`, `status`, `resume`, `close`. Plain aliases (`click`, `move`, `drag`, `done`) are accepted so any grounding model is at home.

## Which model

Reading a screenshot is not enough: placing a click needs a model trained on GUI *grounding*. Claude Sonnet / Opus 4+, GPT-5 and the OpenAI CUA line, Gemini 2.5+, Qwen-VL, UI-TARS qualify. A plain vision model sees the screen but misses buttons; a text model sees nothing.

- Settings > Models > Task routing > **Desktop control** (`model_routes.computer`) names the preset used when a turn asks to drive the desktop ("clique sur le bouton Enregistrer dans Excel", "open the app Blender and ..."). The route only applies while the tool is enabled and the chat is on Auto. `navin computer enable --model <preset>` sets it from the CLI, `/pilot computer` switches by hand.
- Each screenshot tells the model where it stands: a text model is told it received no image and must stop; a vision model without grounding is told to prefer `snapshot` refs and shortcuts and to suggest a better model.
- On the direct Anthropic provider, `anthropic_native: true` sends the tool as Claude's server-defined `computer` tool (same name, same actions, declared display size) with the matching beta header. Other providers always receive the JSON schema.

## Safety

- **Approval gate.** `ask: destructive` (default) confirms destructive shortcuts (`alt+F4`, `ctrl+w`, `win+r`, `Delete` on a selection) and typed commands that look dangerous (`rm -rf`, `sudo`, `git push --force`, `Remove-Item -Recurse`, card numbers). `ask: always` confirms every action.
- **Takeover.** The user moving the mouse pauses the agent; the WebUI "Agent desktop" panel lets the user click and type directly. Parking the cursor in the top-left corner stops the run.
- **Application policies.** `protected_apps` (password managers by default) hide the screen and block every action while such a window is in front. `blocked_apps` / `allowed_apps` / `ask_apps` shape what the agent may touch.
- **Kill switch.** `navin computer stop` halts every desktop action immediately, from any terminal; `navin computer go` releases it.
- **Audit trail.** `navin computer audit` lists sessions; `navin computer audit <session>` replays actions, denials and screenshots.
- **Dedicated mode.** `session_mode: dedicated` refuses to run unless the display is reserved for the agent (`navin computer display start` creates an Xvfb display on Linux; a VM or a second RDP session elsewhere), so `ask: never` can be used safely.
- Screen content is untrusted input: instructions visible on screen are data, not orders. The agent never types passwords, tokens or card numbers it was not given in the chat.

## Related

- Skill: `computer-use` (loaded automatically when the tool is on)
- Browser automation: the `browser` tool and the `playwright-browser` skill
- Mobile devices: [Mobile Agent](./mobile.md)
