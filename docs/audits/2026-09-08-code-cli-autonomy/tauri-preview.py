"""Exercise the compiled Tauri WebKit shell with the real Code tools locally."""
import asyncio
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
from contextlib import suppress
from pathlib import Path

from aiohttp import web
from loguru import logger
from playwright.async_api import async_playwright

ROOT = Path('/home/aymen/projects/deploy7/navin-ai-v2')
sys.path.insert(0, str(ROOT))
from navin.agent.tools import browser, computer
from navin.agent.tools.context import RequestContext, request_context
from navin.bus.queue import MessageBus
from navin.bus.events import INBOUND_META_RUNTIME_CONTROL, RUNTIME_CONTROL_ACK
from navin.channels.websocket import WebSocketChannel, WebSocketConfig
from navin.cli.computer import _xvfb_transport_args
from navin.computer.x11 import X11Backend
from navin.config.loader import save_config, set_config_path
from navin.config.schema import Config
from navin.security.workspace_access import bind_workspace_scope, build_workspace_scope, reset_workspace_scope
from navin.session.manager import SessionManager
from navin.webui.gateway_services import build_gateway_services
from navin.webui.transcript import write_session_messages_as_transcript

CHROME = '/home/aymen/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome'
FORM = '<html><body style="padding:60px;font:24px sans-serif"><h1>Code desktop validation</h1><input id="entry" style="font:24px sans-serif;width:650px;height:60px"></body></html>'


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


async def main():
    out = Path(tempfile.mkdtemp(prefix='navin-code-tauri-'))
    logger.remove()
    logger.add(out / 'gateway.log', level='DEBUG')
    report = {'artifacts': str(out), 'checks': [], 'errors': [], 'external_model_calls': 0}
    print('ARTIFACTS:', out, flush=True)
    project = out / 'project'
    project.mkdir()
    project_home = out / 'projects-home'
    project_home.mkdir()
    (project / 'sample.py').write_text('def greeting(name):\n    return f"Bonjour {name}"\n')
    set_config_path(out / 'config.json')
    config = Config()
    config.agents.defaults.workspace = str(project_home)
    config.gateway.heartbeat.enabled = False
    config.tools.restrict_to_workspace = False
    port = free_port()
    ws = WebSocketConfig(host='127.0.0.1', port=port, token=secrets.token_hex(24))
    config.channels.websocket = ws.model_dump(by_alias=True)
    save_config(config)
    bus = MessageBus()
    sessions = SessionManager(project)
    sessions.legacy_sessions_dir = out / 'no-legacy'
    session = sessions.get_or_create('websocket:tauri-preview')
    session.metadata.update(webui=True, product_module='code', title='Validation Tauri')
    session.metadata['workspace_scope'] = build_workspace_scope(project, 'full', source_channel='websocket').payload()
    session.add_message('user', 'Vérifie ce projet desktop.')
    session.add_message('assistant', 'Validation Tauri\n\n| Outil | État |\n| --- | --- |\n| Code | Prêt |\n\n```python\ndef greeting(name):\n    return f"Bonjour {name}"\n```\n\nOuvrir [sample.py](sample.py).')
    sessions.save(session)
    write_session_messages_as_transcript(session.key, session.messages)
    command = {}
    futures = {}
    seq = 0
    cors = {'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'content-type'}

    async def commands(_request):
        return web.json_response(command, headers=cors)

    async def result(request):
        if request.method == 'OPTIONS':
            return web.Response(headers=cors)
        payload = await request.json()
        future = futures.pop(payload['id'], None)
        if future is not None and not future.done():
            future.set_result(payload)
        return web.json_response({'ok': True}, headers=cors)

    async def form(_request):
        return web.Response(text=FORM, content_type='text/html')

    fixture = web.Application()
    fixture.router.add_get('/command', commands)
    fixture.router.add_route('*', '/result', result)
    fixture.router.add_get('/', form)
    runner = web.AppRunner(fixture)
    await runner.setup()
    fixture_port = free_port()
    await web.TCPSite(runner, '127.0.0.1', fixture_port).start()
    endpoint = f'http://127.0.0.1:{fixture_port}'
    dist = out / 'dist'
    shutil.copytree('/tmp/navin-code-preview-dist', dist)
    # Test-only WebView driver. The product bundle and its controls are unchanged.
    probe = '''let last=0,busy=false;setInterval(async()=>{if(busy)return;busy=true;try{const c=await(await fetch(ENDPOINT+'/command')).json();if(c.id&&c.id!==last){last=c.id;let r;try{r={id:c.id,value:await(new Function('return (async()=>{'+c.source+'})()'))()};}catch(e){r={id:c.id,error:String(e),body:document.body.innerText.slice(0,6000)};}await fetch(ENDPOINT+'/result',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(r)});}}catch(e){}finally{busy=false;}},100);'''.replace('ENDPOINT', json.dumps(endpoint))
    (dist / 'assets/tauri-probe.js').write_text(probe)
    index = dist / 'index.html'
    index.write_text(index.read_text().replace('</body>', '<script src="/assets/tauri-probe.js"></script></body>'))
    gateway = build_gateway_services(config=ws, bus=bus, session_manager=sessions,
        static_dist_path=dist, workspace_path=project_home, default_restrict_to_workspace=False,
        runtime_model_name=lambda:'local-validation', runtime_surface='native', runtime_capabilities_overrides={})
    channel = WebSocketChannel(ws, bus, gateway=gateway)
    serving = asyncio.create_task(channel.start())

    async def relay():
        while True:
            await channel.send(await bus.consume_outbound())

    async def state_queries():
        while True:
            message = await bus.consume_inbound()
            ack = message.metadata.get(RUNTIME_CONTROL_ACK)
            if message.metadata.get(INBOUND_META_RUNTIME_CONTROL) and ack is not None and not ack.done():
                ack.set_result([])

    background = [asyncio.create_task(relay()), asyncio.create_task(state_queries())]
    ctx = RequestContext(channel='websocket', chat_id='tauri-preview', session_key=session.key, turn_id='preview')
    scope = bind_workspace_scope(build_workspace_scope(project, 'full', source_channel='websocket'))
    displays = [':' + str(710 + secrets.randbelow(100)), ':' + str(820 + secrets.randbelow(100))]
    xvfb = []
    tauri = None
    native = None
    shell_backend = None

    async def evaluate(source, timeout=25):
        nonlocal seq
        seq += 1
        future = asyncio.get_running_loop().create_future()
        futures[seq] = future
        command.clear()
        command.update(id=seq, source=source)
        payload = await asyncio.wait_for(future, timeout)
        assert 'error' not in payload, payload
        return payload.get('value')

    async def until(source, timeout=20):
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            value = await evaluate('return Boolean(' + source + ');')
            if value:
                return
            await asyncio.sleep(.2)
        body = await evaluate('return document.body.innerText;')
        raise AssertionError((source, body))

    async def click_button(pattern, live=False):
        root = 'document.querySelector("[data-testid=agent-live-view]")' if live else 'document'
        await evaluate('const root=' + root + '; const b=Array.from(root.querySelectorAll("button,[role=button]")).find(b=>new RegExp(' + json.dumps(pattern) + ').test([b.innerText,b.title,b.getAttribute("aria-label")].join(" ")));if(!b)throw Error("button missing");b.click();return true;')

    async def wait_state(predicate):
        for _ in range(120):
            if predicate():
                return
            await asyncio.sleep(.05)
        raise AssertionError('Backend state did not change')

    async def screenshot(name):
        shot = await asyncio.to_thread(shell_backend.screenshot)
        (out / name).write_bytes(shot.png)

    try:
        for display, size in zip(displays, ['1440x1000x24', '1280x800x24']):
            proc = subprocess.Popen(['Xvfb', display, '-screen', '0', size, '-nolisten', 'tcp', *_xvfb_transport_args()], stdout=(out / ('xvfb-' + display[1:] + '.log')).open('w'), stderr=subprocess.STDOUT)
            xvfb.append(proc)
        await asyncio.sleep(.8)
        shell_backend = X11Backend(display=displays[0])
        env = {**os.environ, 'DISPLAY': displays[0], 'WAYLAND_DISPLAY': '', 'GDK_BACKEND': 'x11',
            'NAVIN_DESKTOP_CONFIG': str(out / 'config.json'), 'XDG_DATA_HOME': str(out / 'native-data'), 'XDG_CACHE_HOME': str(out / 'native-cache')}
        tauri = subprocess.Popen([str(ROOT / 'desktop/src-tauri/target/debug/navin-desktop'), str(project)], cwd=ROOT, env=env, stdout=(out / 'tauri.log').open('w'), stderr=subprocess.STDOUT)
        identity = await evaluate('return {tauri:Boolean(window.__TAURI__?.core?.invoke),ua:navigator.userAgent,url:location.href};', timeout=40)
        assert identity['tauri'], identity
        report['webview'] = identity
        await evaluate('location.hash="/code?chat=websocket%3Atauri-preview&project="+encodeURIComponent(' + json.dumps(str(project)) + ');return true;')
        await until('document.querySelector(".markdown-content table")')
        await click_button('^Validation Tauri')
        await evaluate('const b=Array.from(document.querySelectorAll("button,[role=button]")).find(b=>/close|dismiss|fermer/i.test([b.title,b.getAttribute("aria-label")].join(" ")));if(b)b.click();return true;')
        (out / 'controls.json').write_text(json.dumps(await evaluate('return Array.from(document.querySelectorAll("button,[role=button],a")).map(b=>({tag:b.tagName,text:b.innerText,aria:b.getAttribute("aria-label"),title:b.title}));'), indent=2))
        await screenshot('tauri-code.png')
        await click_button('sample.py')
        await until('document.querySelector(".cm-content")?.innerText.includes("def greeting")')
        report['checks'].append('native Tauri WebKit loads Code, Markdown and the real file editor')
        print('PASS: native Code UI', flush=True)
        tool = browser.BrowserTool(config=browser.BrowserToolConfig(executable_path=CHROME, live_view=True), workspace=project, bus=bus)
        with request_context(ctx):
            assert not getattr(await tool.execute(action='navigate', url=endpoint), 'is_error', False)
            assert not getattr(await tool.execute(action='type', selector='#entry', text='agent'), 'is_error', False)
        await until('document.querySelector("[data-testid=agent-live-view] img")?.naturalWidth>0')
        remote = browser._SESSIONS[session.key]
        await click_button('Prendre la main|Take control', live=True)
        await wait_state(lambda: remote.user_control)
        await until('Array.from(document.querySelectorAll("[data-testid=agent-live-view] button")).some(el=>/Rendre la main|Return control/.test(el.innerText))')
        await evaluate('const el=document.querySelector("[data-testid=agent-live-view] img");el.focus();el.dispatchEvent(new KeyboardEvent("keydown",{key:"k",bubbles:true}));return true;')
        for _ in range(100):
            if await remote.page.locator('#entry').input_value() == 'agentk':
                break
            await asyncio.sleep(.05)
        assert await remote.page.locator('#entry').input_value() == 'agentk'
        await click_button('Rendre la main|Return control', live=True)
        await wait_state(lambda: not remote.user_control)
        with request_context(ctx):
            assert not getattr(await tool.execute(action='type', selector='#entry', text='Reprise Tauri'), 'is_error', False)
        await asyncio.sleep(.5)
        await screenshot('tauri-browser-live.png')
        report['checks'].append('Tauri browser live frames, manual keyboard input and agent resumption work')
        print('PASS: native browser live', flush=True)
        await click_button('Fermer cette session|Close this session', live=True)
        await wait_state(lambda: session.key not in browser._SESSIONS)
        async with async_playwright() as pw:
            native = await pw.chromium.launch(executable_path=CHROME, headless=False,
                env={**os.environ,'DISPLAY':displays[1],'WAYLAND_DISPLAY':''},
                args=['--no-sandbox','--disable-dev-shm-usage','--ozone-platform=x11','--kiosk','--window-size=1280,800','--window-position=0,0'])
            page = await native.new_page(no_viewport=True)
            await page.set_content(FORM)
            await page.bring_to_front()
            await page.locator('#entry').focus()
            await page.wait_for_timeout(500)
            config.tools.computer.enabled = True
            config.tools.computer.backend = 'x11'
            config.tools.computer.display = displays[1]
            config.tools.computer.session_mode = 'dedicated'
            config.tools.computer.ask = 'never'
            config.tools.computer.live_view = True
            config.tools.computer.audit_log = False
            config.tools.computer.audit_screenshots = False
            config.tools.computer.settle_ms = 20
            save_config(config)
            desktop = computer.ComputerTool(config=config.tools.computer, bus=bus)
            with request_context(ctx):
                assert isinstance(await desktop.execute(action='screenshot'), list)
                assert not getattr(await desktop.execute(action='type', text='desktop'), 'is_error', False)
            assert await page.locator('#entry').input_value() == 'desktop', await page.locator('#entry').input_value()
            await until('document.querySelector("[data-testid=agent-live-view] img")?.naturalWidth>0')
            remote_desktop = computer._SESSIONS[session.key]
            await click_button('Prendre la main|Take control', live=True)
            await wait_state(lambda: remote_desktop.user_control)
            await until('Array.from(document.querySelectorAll("[data-testid=agent-live-view] button")).some(el=>/Rendre la main|Return control/.test(el.innerText))')
            await evaluate('const el=document.querySelector("[data-testid=agent-live-view] img");el.focus();el.dispatchEvent(new KeyboardEvent("keydown",{key:"r",bubbles:true}));return true;')
            for _ in range(100):
                if await page.locator('#entry').input_value() == 'desktopr':
                    break
                await asyncio.sleep(.05)
            assert await page.locator('#entry').input_value() == 'desktopr', await page.locator('#entry').input_value()
            await click_button('Rendre la main|Return control', live=True)
            await wait_state(lambda: not remote_desktop.user_control)
            with request_context(ctx):
                assert not getattr(await desktop.execute(action='type', text=' reprise'), 'is_error', False)
            assert await page.locator('#entry').input_value() == 'desktopr reprise'
            await asyncio.sleep(.5)
            await screenshot('tauri-computer-live.png')
            report['checks'].append('Tauri controls the native X11 desktop and returns control to the agent')
            print('PASS: native computer live', flush=True)
            await native.close()
            native = None
        assert tauri.poll() is None
        report['ok'] = True
    except BaseException as exc:
        report['failure'] = repr(exc)
        if shell_backend is not None:
            with suppress(Exception):
                await screenshot('tauri-failure.png')
        raise
    finally:
        if tauri is not None and tauri.poll() is None:
            tauri.terminate()
            await asyncio.to_thread(tauri.wait, 10)
        await browser.shutdown_browser_sessions()
        await computer.shutdown_computer_sessions()
        if native is not None:
            await native.close()
        if shell_backend is not None:
            shell_backend.close()
        for proc in xvfb:
            if proc.poll() is None:
                proc.terminate()
                await asyncio.to_thread(proc.wait, 5)
        reset_workspace_scope(scope)
        for task in background:
            task.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        await channel.stop()
        await asyncio.gather(serving, return_exceptions=True)
        await runner.cleanup()
        (out / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False), flush=True)


asyncio.run(main())
