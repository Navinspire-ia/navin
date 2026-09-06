# Command output compaction

Shell output from the agent (`exec`) is compacted before it is added to the model context. Progress noise, repeated lines, and oversized tables are reduced; failures and status that matter are kept. Compaction is built into Navin - no external filter to install.

Live terminal output is not altered while a command is running. Compaction applies to the finalized result the model receives.

## Covered commands

| Area | Examples | Result for the model |
| --- | --- | --- |
| Git | `status`, `diff`, `log`, `push` | Grouped status; progress stripped |
| Tests | pytest, jest/vitest, cargo/go test, playwright | Failures and summary first |
| Python | ruff, mypy, pip, uv | Findings or install summary |
| JS / build | eslint, tsc, next, npm install | Errors first |
| Containers | docker / podman | Essential columns; deduplicated logs |
| Kubernetes | kubectl, oc, helm | Compact tables; quieter describe |
| Infra | terraform, make, gradle | Errors and summary |
| Network | curl, gh | Short bodies or tables |
| Other | unmatched commands | ANSI / progress / duplicate cleanup |

Shell wrappers such as `bash -lc '…'` are resolved so the underlying command is classified correctly.

## Timing

| Situation | Compaction |
| --- | --- |
| Synchronous `exec` | After the process exits |
| Background `exec` | When the process completes |
| `write_stdin` session | When the process completes |
| Streaming live output | None (terminal stays as-is) |

When the compacted text is shorter than the original, the full log is stored under `.navin/tool-results/exec/` and the tool result notes the path.

## Cost

Compaction lowers tokens from **tool output** on command-heavy sessions. System prompts, tool schemas, and chat history are unchanged. It is a context hygiene feature, not a full-bill discount.

## Related

- [Code agent](./code-agent.md)
- [Workbench](./workbench.md)
- [Editor AI](./editor-ai.md)
