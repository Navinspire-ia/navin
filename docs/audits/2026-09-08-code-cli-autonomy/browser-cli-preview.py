"""Exercise the unchanged Code UI and current tools in an isolated workspace."""
import asyncio
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
from contextlib import suppress
from pathlib import Path

from aiohttp import ClientSession, web
from loguru import logger
from playwright.async_api import async_playwright, expect

ROOT = Path('/home/aymen/projects/deploy7/navin-ai-v2')
sys.path.insert(0, str(ROOT))

from navin.agent.tools import browser, computer
from navin.agent.tools.context import RequestContext, request_context
from navin.bus.queue import MessageBus
from navin.bus.events import INBOUND_META_RUNTIME_CONTROL, RUNTIME_CONTROL_ACK, RUNTIME_CONTROL_APPROVALS_QUERY, RUNTIME_CONTROL_CHOICES_QUERY, RUNTIME_CONTROL_SUBAGENTS_QUERY
from navin.channels.websocket import WebSocketChannel, WebSocketConfig
from navin.config.loader import load_config, save_config, set_config_path
from navin.config.schema import Config
from navin.security.workspace_access import bind_workspace_scope, build_workspace_scope, reset_workspace_scope
from navin.session.manager import SessionManager
from navin.webui.gateway_services import build_gateway_services
from navin.webui.transcript import write_session_messages_as_transcript

CHROME = '/home/aymen/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome'
FORM = '''<!doctype html><html lang="fr"><meta charset="utf-8"><title>Test Code</title>
<style>body{font:24px sans-serif;padding:60px;background:#f4f7fb;color:#163052}input,button{font:24px sans-serif;padding:12px;margin:12px 0}input{width:500px}output{display:block}</style>
<h1>Validation des outils Code</h1><label for="entry">Texte du test</label><br><input id="entry"><br>
<button id="save" onclick="document.querySelector('output').textContent=document.querySelector('input').value">Valider</button><output></output></html>'''


async def main():
    logger.remove()
    out = Path(tempfile.mkdtemp(prefix='navin-code-preview-'))
    logger.add(out / 'gateway.log', level='DEBUG')
    project = out / 'project'
    project.mkdir()
    project_home = out / 'projects-home'
    project_home.mkdir()
    (project / 'sample.py').write_text('def greeting(name):\n    return f"Bonjour {name}"\n', encoding='utf-8')
    (project / 'README.md').write_text('# Projet de validation Code\n', encoding='utf-8')
    set_config_path(out / 'config.json')
    config = Config()
    config.agents.defaults.workspace = str(project_home)
    config.gateway.heartbeat.enabled = False
    config.tools.restrict_to_workspace = False
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    ws_config = WebSocketConfig(host='127.0.0.1', port=port, token=secrets.token_hex(24))
    config.channels.websocket = ws_config.model_dump(by_alias=True)
    save_config(config)
    bus = MessageBus()
    sessions = SessionManager(project)
    sessions.legacy_sessions_dir = out / 'no-legacy'
    session = sessions.get_or_create('websocket:code-preview')
    session.metadata.update(webui=True, product_module='code', title='Validation Code')
    session.metadata['workspace_scope'] = build_workspace_scope(project, 'full', source_channel='websocket').payload()
    session.add_message('user', 'Vérifie ce projet de démonstration.')
    session.add_message('assistant', '## Validation du module Code\n\nParcours local de vérification.\n\n| Outil | État |\n| --- | --- |\n| Skills | Chargés |\n| Fichiers | Lisibles |\n\n```python\ndef greeting(name):\n    return f"Bonjour {name}"\n```\n\nOuvrir [sample.py](sample.py).')
    sessions.save(session)
    write_session_messages_as_transcript(session.key, session.messages)
    gateway = build_gateway_services(
        config=ws_config, bus=bus, session_manager=sessions,
        static_dist_path=Path('/tmp/navin-code-preview-dist'), workspace_path=project_home,
        default_restrict_to_workspace=False, runtime_model_name=lambda: 'validation-locale',
        runtime_surface='browser', runtime_capabilities_overrides={},
    )
    channel = WebSocketChannel(ws_config, bus, gateway=gateway)
    serving = asyncio.create_task(channel.start())

    async def relay():
        while True:
            await channel.send(await bus.consume_outbound())

    relaying = asyncio.create_task(relay())
    async def idle_agent_state():
        # This fixture has no model turn or pending questions. Reconnect
        # queries still need an answer so they do not hold the socket queue.
        while True:
            message = await bus.consume_inbound()
            metadata = message.metadata
            if metadata.get(INBOUND_META_RUNTIME_CONTROL) in {
                RUNTIME_CONTROL_APPROVALS_QUERY, RUNTIME_CONTROL_CHOICES_QUERY,
                RUNTIME_CONTROL_SUBAGENTS_QUERY,
            }:
                ack = metadata.get(RUNTIME_CONTROL_ACK)
                if ack is not None and not ack.done():
                    ack.set_result([])

    state_queries = asyncio.create_task(idle_agent_state())
    fixture = web.Application()
    async def form(_request):
        return web.Response(text=FORM, content_type='text/html')
    fixture.router.add_get('/', form)
    fixture_runner = web.AppRunner(fixture)
    await fixture_runner.setup()
    fixture_site = web.TCPSite(fixture_runner, '127.0.0.1', 0)
    await fixture_site.start()
    fixture_port = fixture_site._server.sockets[0].getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    ctx = RequestContext(channel='websocket', chat_id='code-preview', session_key='websocket:code-preview', turn_id='preview')
    report = {'artifacts': str(out), 'checks': [], 'errors': [], 'model_calls': 0}
    xvfb = None
    display = None
    controller = None
    native = None
    async def cli(*args):
        proc = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'navin', 'computer', *args, '--config', str(out / 'config.json'),
            cwd=ROOT, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), 45)
        result = stdout.decode('utf-8', errors='replace')
        name = args[0] + ('-' + args[1] if args[0] == 'display' else '')
        (out / ('cli-' + name + '.txt')).write_text(result, encoding='utf-8')
        assert proc.returncode == 0, result
        return result
    scope = bind_workspace_scope(build_workspace_scope(project, 'full', source_channel='websocket'))
    try:
        async with ClientSession() as http:
            for _ in range(100):
                try:
                    async with http.get(base + '/health') as response:
                        if response.status == 200:
                            break
                except OSError:
                    pass
                await asyncio.sleep(0.1)
        async with async_playwright() as pw:
            controller = await pw.chromium.launch(executable_path=CHROME, headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
            ui = await controller.new_page(viewport={'width': 1440, 'height': 1000}, locale='fr-FR', reduced_motion='reduce', permissions=['clipboard-read', 'clipboard-write'])
            ui.on('pageerror', lambda exc: report['errors'].append(str(exc)))
            await ui.goto(base + '/#/code?chat=websocket%3Acode-preview', wait_until='domcontentloaded')
            await ui.get_by_role('button', name='Validation Code', exact=True).click(timeout=20000)
            await ui.wait_for_timeout(2000)
            dismiss = ui.get_by_role('button', name='Fermer', exact=True)
            if await dismiss.count():
                await dismiss.click()
            (out / 'initial-body.txt').write_text(await ui.locator('body').inner_text(), encoding='utf-8')
            (out / 'controls.json').write_text(json.dumps(await ui.locator('button').evaluate_all('(els)=>els.map(e=>({text:e.innerText,aria:e.getAttribute("aria-label"),title:e.title}))'), ensure_ascii=False, indent=2))
            await ui.screenshot(path=str(out / 'code-initial.png'), full_page=True)
            await expect(ui.get_by_text('Validation du module Code', exact=True)).to_be_visible(timeout=20000)
            await expect(ui.locator('.markdown-content table')).to_be_visible()
            copy = ui.locator('.markdown-content button').first
            await copy.click()
            await expect(copy).to_contain_text(re.compile('Copié|Copied'))
            assert 'def greeting' in await ui.evaluate('navigator.clipboard.readText()')
            report['checks'].append('unchanged Code chat renders tables and fenced code; copy control works')
            print('PASS: Markdown and clipboard', flush=True)
            file_button = ui.get_by_role('button', name=re.compile('sample.py')).first
            await file_button.click()
            await expect(ui.locator('.cm-content').first).to_contain_text('def greeting', timeout=15000)
            report['checks'].append('chat file reference opens the real project file in the editor')
            print('PASS: project file opens in Code', flush=True)
            await ui.screenshot(path=str(out / 'code-editor.png'), full_page=True)

            tool = browser.BrowserTool(config=browser.BrowserToolConfig(executable_path=CHROME, live_view=True), workspace=project, bus=bus)
            with request_context(ctx):
                result = await tool.execute(action='navigate', url=f'http://127.0.0.1:{fixture_port}/')
                assert not getattr(result, 'is_error', False), str(result)
                result = await tool.execute(action='type', selector='#entry', text='agent')
                assert not getattr(result, 'is_error', False), str(result)
            live = ui.get_by_test_id('agent-live-view')
            await expect(live).to_be_visible(timeout=20000)
            take = live.get_by_role('button', name=re.compile('Prendre la main|Take control'))
            await take.click()
            remote = browser._SESSIONS[ctx.session_key]
            for _ in range(100):
                if remote.user_control:
                    break
                await asyncio.sleep(0.05)
            assert remote.user_control, await live.inner_text()
            with request_context(ctx):
                blocked = await tool.execute(action='type', selector='#entry', text='must not appear')
                assert getattr(blocked, 'is_error', False)
            image = live.locator('img')
            await expect(image).to_be_visible()
            await image.focus()
            await ui.keyboard.type(' live', delay=100)
            await expect(remote.page.locator('#entry')).to_have_value('agent live', timeout=15000)
            await live.get_by_role('button', name=re.compile('Rendre la main|Return control')).click()
            for _ in range(100):
                if not remote.user_control:
                    break
                await asyncio.sleep(0.05)
            with request_context(ctx):
                assert not getattr(await tool.execute(action='type', selector='#entry', text='reprise agent'), 'is_error', False)
                assert not getattr(await tool.execute(action='click', selector='#save'), 'is_error', False)
            await expect(remote.page.locator('output')).to_have_text('reprise agent')
            await ui.wait_for_timeout(500)
            await ui.screenshot(path=str(out / 'code-browser-live.png'), full_page=True)
            report['checks'].append('real Chromium automation, live takeover, user typing and agent resumption work through Code controls')
            print('PASS: browser live takeover and resumption', flush=True)
            await live.get_by_role('button', name=re.compile('Fermer cette session|Close this session')).click()
            for _ in range(100):
                if ctx.session_key not in browser._SESSIONS:
                    break
                await asyncio.sleep(0.05)
            assert ctx.session_key not in browser._SESSIONS
            report['checks'].append('closing the live browser removes its exact backend session')

            assert shutil.which('Xvfb'), 'Xvfb is required for the dedicated desktop test'
            display = ':' + str(200 + secrets.randbelow(500))
            await cli('display', 'start', '--display', display, '--size', '1280x800', '--no-wm')
            await cli('enable', '--backend', 'x11', '--dedicated', '--display', display, '--ask', 'never', '--live-view')
            await cli('status')
            await cli('doctor')
            await cli('screenshot', str(out / 'cli-desktop.png'))
            assert (out / 'cli-desktop.png').read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
            report['checks'].append('actual CLI enable, status, doctor and screenshot succeed with a separate instance config')
            native = await pw.chromium.launch(executable_path=CHROME, headless=False, env={**os.environ, 'DISPLAY': display, 'WAYLAND_DISPLAY': ''}, args=['--no-sandbox', '--disable-dev-shm-usage', '--ozone-platform=x11', '--kiosk', '--window-size=1280,800', '--window-position=0,0'])
            desk = await native.new_page(no_viewport=True)
            await desk.set_content(FORM)
            await desk.locator('#entry').focus()
            await desk.wait_for_timeout(500)
            desktop_config = load_config().tools.computer
            desktop_config.settle_ms = 20
            desktop_config.live_view_min_frame_ms = 0
            desktop_config.audit_log = False
            desktop_config.audit_screenshots = False
            desktop = computer.ComputerTool(config=desktop_config, bus=bus)
            with request_context(ctx):
                shot = await desktop.execute(action='screenshot')
                assert isinstance(shot, list), str(shot)[:1000]
                typed = await desktop.execute(action='type', text='desktop agent')
                assert not getattr(typed, 'is_error', False), str(typed)
            await expect(desk.locator('#entry')).to_have_value('desktop agent')
            await expect(live).to_be_visible(timeout=20000)
            await live.get_by_role('button', name=re.compile('Prendre la main|Take control')).click()
            desktop_session = computer._SESSIONS[ctx.session_key]
            for _ in range(100):
                if desktop_session.user_control:
                    break
                await asyncio.sleep(0.05)
            assert desktop_session.user_control, await live.inner_text()
            await live.locator('img').focus()
            await ui.keyboard.type(' live', delay=100)
            await expect(desk.locator('#entry')).to_have_value('desktop agent live', timeout=15000)
            await live.get_by_role('button', name=re.compile('Rendre la main|Return control')).click()
            for _ in range(100):
                if not desktop_session.user_control:
                    break
                await asyncio.sleep(0.05)
            with request_context(ctx):
                resumed = await desktop.execute(action='type', text=' reprise')
                assert not getattr(resumed, 'is_error', False), str(resumed)
            await expect(desk.locator('#entry')).to_have_value('desktop agent live reprise')
            await ui.screenshot(path=str(out / 'code-desktop-live.png'), full_page=True)
            report['checks'].append('real Linux X11 desktop, live keyboard input and agent resumption work on a dedicated display')
            await cli('stop', 'validation pause')
            with request_context(ctx):
                stopped = await desktop.execute(action='type', text='must not appear')
                assert getattr(stopped, 'is_error', False), str(stopped)
            await cli('go')
            with request_context(ctx):
                assert not getattr(await desktop.execute(action='type', text=' cli'), 'is_error', False)
            await expect(desk.locator('#entry')).to_have_value('desktop agent live reprise cli')
            await cli('disable')
            assert not load_config().tools.computer.enabled
            report['checks'].append('actual CLI stop/go pauses and resumes the active Code desktop session; disable persists')
            await native.close()
            await controller.close()
            assert not report['errors'], report['errors']
            report['ok'] = True
    except BaseException as exc:
        report['failure'] = str(exc)
        (out / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'failure': str(exc), 'artifacts': str(out)}, ensure_ascii=False), flush=True)
        raise
    finally:
        if native is not None:
            await native.close()
        if controller is not None:
            await controller.close()
        await browser.shutdown_browser_sessions()
        await computer.shutdown_computer_sessions()
        if display is not None:
            await cli('display', 'stop', '--display', display)
        reset_workspace_scope(scope)
        await fixture_runner.cleanup()
        relaying.cancel()
        state_queries.cancel()
        with suppress(asyncio.CancelledError):
            await relaying
        with suppress(asyncio.CancelledError):
            await state_queries
        await channel.stop()
        with suppress(asyncio.CancelledError):
            await serving
        if xvfb is not None:
            xvfb.terminate()
            xvfb.wait(timeout=5)
        (out / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False), flush=True)


asyncio.run(main())
