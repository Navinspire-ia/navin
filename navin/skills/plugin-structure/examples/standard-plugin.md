# Standard plugin

A testing helper with two skills, two commands and one hook.

```
test-kit/
├── plugin.json
├── README.md
├── skills/
│   ├── test-strategy/
│   │   ├── SKILL.md
│   │   └── references/
│   │       └── fixtures.md
│   └── flaky-tests/
│       └── SKILL.md
├── commands/
│   ├── test-plan.md
│   └── fix-flaky.md
└── hooks/
    ├── hooks.json
    └── scripts/
        └── require-tests.sh
```

`plugin.json`:

```json
{
  "name": "test-kit",
  "version": "1.2.0",
  "description": "Plan tests, fix flaky tests, keep the suite green",
  "author": {"name": "QA Guild"},
  "license": "MIT"
}
```

`skills/test-strategy/SKILL.md` (start):

```markdown
---
name: test-strategy
description: Choose what to test and at which level (unit, integration, end-to-end). Use when the user asks to "plan tests", "what should I test", "improve coverage", or before writing tests for a new feature.
---

# Test Strategy

Start from the behaviour that would hurt most if it broke ...
Fixture conventions: see `references/fixtures.md`.
```

`commands/test-plan.md`:

```markdown
---
description: Write a test plan for a file or feature
argument-hint: <file-or-feature>
---

Load the `test-strategy` skill. Read @$1 if it is a file, otherwise locate the
feature named "$ARGUMENTS" with grep. Produce a test plan table: behaviour,
level, cases, fixtures. Ask before writing any test file.
```

`hooks/hooks.json`:

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash ${NAVIN_PLUGIN_ROOT}/hooks/scripts/require-tests.sh",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

`hooks/scripts/require-tests.sh`:

```bash
#!/usr/bin/env bash
# Keep the agent working when source files changed but no test file did.
set -euo pipefail
input=$(cat)
active=$(printf '%s' "$input" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("stop_hook_active", False))')
[ "$active" = "True" ] && exit 0          # already asked once: let it stop
cd "${NAVIN_PROJECT_DIR:-.}"
changed=$(git status --porcelain 2>/dev/null | awk '{print $2}')
[ -z "$changed" ] && exit 0
if printf '%s\n' "$changed" | grep -qE '\.(py|ts|js)$' && ! printf '%s\n' "$changed" | grep -qE '(^|/)(test_|tests?/|.*\.test\.)'; then
  echo "Source files changed without any test change. Add or update tests, run them, then finish." >&2
  exit 2
fi
exit 0
```

Remember `chmod +x hooks/scripts/require-tests.sh`.
