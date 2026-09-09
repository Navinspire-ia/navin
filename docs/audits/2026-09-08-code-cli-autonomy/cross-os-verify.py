"""Run Navin's actual verification tool against this task's changed files."""

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path('/home/aymen/projects/deploy7/navin-ai-v2')
sys.path.insert(0, str(ROOT))

from navin.agent.tools.quality import VerifyTool
from navin.config.loader import set_config_path
from navin.quality import testing

_run_one = testing._run_one

def log_runner(name, spec, root, target):
    print('verify runner:', name, testing._runner_argv(spec, root), 'target:', target, flush=True)
    result = _run_one(name, spec, root, target)
    print(result.summary(), flush=True)
    return result

testing._run_one = log_runner

PATHS = [
    'navin/agent/project_instructions.py',
    'navin/agent/context.py',
    'navin/agent/project_agents.py',
    'navin/agent/skills.py',
    'navin/agent/tool_surface.py',
    'navin/agent/runner.py',
    'navin/agent/loop.py',
    'navin/agent/tools/file_state.py',
    'navin/agent/tools/filesystem.py',
    'navin/agent/tools/skill_catalog.py',
    'navin/agent/tools/browser.py',
    'navin/agent/tools/computer.py',
    'navin/utils/runtime.py',
    'navin/cli/computer.py',
    'navin/quality/testing.py',
    'navin/templates/agent/tool_contract_slim.md',
    'webui/src/hooks/useVoiceSession.ts',
    'tests/test_code_autonomy.py',
    'tests/test_cli_computer.py',
    'navin/session/turn_recovery.py',
    'navin/session/webui_turns.py',
    'navin/cli/commands.py',
    'desktop/src-tauri/src/main.rs',
    'tests/test_turn_recovery.py',
    'tests/test_stop_command.py',
    'navin/browser_runtime.py',
    'navin/computer/smoke.py',
    'navin/documents/_chromium.py',
    'navin/agent/tools/browser_use_bridge.py',
    'tests/test_desktop_browser_runtime.py',
    'packaging/smoke-test.sh',
    'packaging/windows/smoke-test.ps1',
    '.github/workflows/ci.yml',
]


async def main():
    data = Path(tempfile.mkdtemp(prefix='navin-code-verify-'))
    set_config_path(data / 'config.json')
    result = await VerifyTool(workspace=ROOT).execute(
        action='check', paths=PATHS, with_tests=True,
        test_target='tests/test_desktop_browser_runtime.py',
    )
    Path('/tmp/navin-code-cross-os-verify.txt').write_text(str(result), encoding='utf-8')
    print(result, flush=True)
    assert str(result).startswith('PASS -'), str(result)


asyncio.run(main())
