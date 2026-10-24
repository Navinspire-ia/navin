"""Exercise the browser, Navin bridge and installed LiteLLM with simulated Bedrock.

Requires Vite on --web-url, Docker, the dev Python environment and Playwright.
The container has no network or AWS credentials. Documentation tools are real;
provider events and the short output tone are simulated. No provider credentials
are loaded or passed to the container.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import struct
import sys
from pathlib import Path

from playwright.async_api import async_playwright
from websockets.asyncio.server import serve

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WORKER = """
import json, sys
from litellm.llms.bedrock.realtime.transformation import BedrockRealtimeConfig
config = BedrockRealtimeConfig()
state = {}
for line in sys.stdin:
    try:
        command = json.loads(line)
        if command['direction'] == 'request':
            output = [json.loads(frame) for frame in config.transform_realtime_request(
                json.dumps(command['event']), model='amazon/nova-2-sonic')]
        else:
            state = config.transform_realtime_response(json.dumps({'event': command['event']}),
                model='amazon/nova-2-sonic', logging_obj=None,
                realtime_response_transform_input=state)
            output = state['response']
        print(json.dumps({'output': output}), flush=True)
    except Exception as exc:
        print(json.dumps({'error': str(exc)}), flush=True)
"""


class Transformer:
    def __init__(self, process):
        self.process = process
        self.lock = asyncio.Lock()

    async def run(self, direction, event):
        async with self.lock:
            self.process.stdin.write((json.dumps({"direction": direction, "event": event}) + "\n").encode())
            await self.process.stdin.drain()
            raw = await asyncio.wait_for(self.process.stdout.readline(), 20)
            if not raw:
                raise RuntimeError("LiteLLM verification container stopped")
            response = json.loads(raw)
            if "error" in response:
                raise RuntimeError(response["error"])
            return response["output"]


class Upstream:
    def __init__(self, transformer, result):
        self.transformer = transformer
        self.result = result
        self.frames = asyncio.Queue()
        self.calls = [("call-docs-1", "live voice"), ("call-docs-2", "subagents communication")]
        self.started = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        frame = await self.frames.get()
        if frame is None:
            raise StopAsyncIteration
        return frame

    async def close(self):
        await self.frames.put(None)

    async def provider(self, event):
        for frame in await self.transformer.run("response", event):
            await self.frames.put(json.dumps(frame))

    async def call(self, index):
        call_id, query = self.calls[index]
        await self.provider({"toolUse": {
            "toolUseId": call_id, "toolName": "navin_docs", "content": json.dumps({"query": query}),
        }})

    async def send(self, raw):
        message = json.loads(raw)
        converted = await self.transformer.run("request", message)
        if message["type"] == "session.update":
            prompt = next(frame["event"]["promptStart"] for frame in converted if "promptStart" in frame["event"])
            assert prompt["audioOutputConfiguration"]["voiceId"] == "ambre", "Automatic French voice must match the avatar"
            self.result["voice"] = prompt["audioOutputConfiguration"]["voiceId"]
            tools = prompt["toolConfiguration"]["tools"]
            assert all(tool["toolSpec"]["name"] for tool in tools), "Empty Bedrock tool name"
            self.result["configuredTools"] = len(tools)
            await self.frames.put(json.dumps({"type": "session.updated"}))
        elif message["type"] == "input_audio_buffer.append":
            audio = base64.b64decode(message["audio"])
            assert len(audio) == 640, "Microphone must be resampled from 24 to 16 kHz"
            assert any("audioInput" in frame["event"] for frame in converted)
            self.result["inputBytes16k"] = len(audio)
            if not self.started:
                self.started = True
                await self.call(0)
        elif message["type"] == "response.create" and not self.started:
            raise AssertionError("The client spoke before receiving microphone audio")
        elif message["type"] == "conversation.item.create":
            item = message.get("item", {})
            if item.get("type") != "function_call_output":
                return
            index = len(self.result["tools"])
            assert item["call_id"] == self.calls[index][0], "Tool result call_id changed"
            output = next(frame["event"]["toolResult"] for frame in converted if "toolResult" in frame["event"])
            parsed = json.loads(output["content"])
            assert isinstance(parsed, dict) and isinstance(parsed.get("result"), str), "Expected JSON-wrapped documentation text"
            assert "https://navin.live/" in parsed["result"], "Expected real documentation results"
            self.result["tools"].append({"call_id": item["call_id"], "textResult": True})
            if index == 0:
                await self.call(1)
            else:
                await self.provider({"contentStart": {"role": "ASSISTANT", "type": "AUDIO", "contentName": "reply"}})
                tone = b"".join(struct.pack("<h", int(2000 * math.sin(2 * math.pi * 440 * n / 24000))) for n in range(2400))
                self.result["expectedOutput"] = base64.b64encode(tone).decode()
                await self.provider({"audioOutput": {"content": self.result["expectedOutput"]}})
                await self.provider({"contentEnd": {"contentName": "reply", "stopReason": "END_TURN"}})


async def main(args):
    from navin.config.schema import Config
    from navin.live.realtime import bridge

    result = {"mode": "local-simulated-bedrock", "tools": [], "errors": []}
    command = ["docker", "run", "--rm", "-i", "--network", "none", "-e", "LITELLM_LOCAL_MODEL_COST_MAP=True",
               "--entrypoint", "python"]
    command += [args.image, "-u", "-c", WORKER]
    process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
    transformer = Transformer(process)
    config = Config()
    config.license.managed_api_base = "https://local-verification.invalid/v1"
    config.providers.navin.api_key = "local-verification-only"
    config.voice.live_realtime_provider = "navin"
    config.voice.live_realtime_model = "amazon/nova-2-sonic"
    try:
        async def connected(ws):
            upstream = Upstream(transformer, result)

            async def connect(_target):
                return upstream

            await bridge(ws, config, connect=connect)

        async with serve(connected, "127.0.0.1", 0) as server, async_playwright() as playwright:
            port = server.sockets[0].getsockname()[1]
            browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                page = await browser.new_page()
                await page.goto(args.web_url, wait_until="domcontentloaded")
                result["browser"] = await page.evaluate(r"""async url => {
                    const { RealtimeLiveSession } = await import('/src/lib/realtime-live.ts');
                    const observations = { tools: [], audio: [], errors: [] };
                    let done;
                    const completed = new Promise(resolve => { done = resolve; });
                    const session = new RealtimeLiveSession({ url, instructions: 'Local integration check', language: 'fr' }, {
                        onTool: async (name, args) => {
                            if (name !== 'navin_docs') throw new Error('Unexpected tool');
                            const boot = await (await fetch('/webui/bootstrap')).json();
                            const { searchNavinDocs } = await import('/src/lib/api.ts');
                            const results = await searchNavinDocs(boot.api_token, args.query);
                            observations.tools.push({ name, args, results: results.length });
                            return results.map(result => `## ${result.title}\n${result.excerpt}\n(${result.url})`).join('\n\n');
                        },
                        onAudio: audio => { observations.audio.push(audio); },
                        onResponseDone: () => done(),
                        onError: (code, detail) => { observations.errors.push({ code, detail }); done(); }
                    });
                    const timeout = setTimeout(() => { observations.timeout = true; done(); }, 15000);
                    try {
                        await session.open();
                        // 20 ms of synthetic PCM at the browser's 24 kHz rate.
                        session.appendAudio(btoa(String.fromCharCode(...new Uint8Array(960))));
                        await completed;
                        return observations;
                    } finally { clearTimeout(timeout); session.close(); }
                }""", f"ws://127.0.0.1:{port}")
            finally:
                await browser.close()
        observed = result["browser"]
        assert not observed.get("timeout"), "Browser exchange timed out"
        assert not observed["errors"], observed["errors"]
        assert [tool["args"]["query"] for tool in observed["tools"]] == ["live voice", "subagents communication"]
        assert all(tool["results"] > 0 for tool in observed["tools"])
        assert len(result["tools"]) == 2
        assert observed["audio"] == [result["expectedOutput"]], "Output audio changed during transport"
        result["passed"] = True
    except Exception as exc:
        result["passed"] = False
        result["errors"].append(str(exc))
    finally:
        process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), 10)
        except TimeoutError:
            process.kill()
            await process.wait()
    result.pop("expectedOutput", None)
    if "browser" in result:
        result["browser"]["audioChunks"] = len(result["browser"].pop("audio"))
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="navin-litellm:realtime-local-check")
    parser.add_argument("--web-url", default="http://127.0.0.1:5173")
    options = parser.parse_args()
    raise SystemExit(asyncio.run(main(options)))
