# security-guidance

Catches security mistakes while the agent writes code, with no setup.

- **Pattern warnings, on by default.** After every `write_file`, `edit_file` or `apply_patch`, the new text is checked against 25 known dangerous patterns. A match adds a short reminder to the tool result, so the agent sees it and fixes the code or explains why it is safe, and shows you a notice. Each file and rule warns once per session.
- **AI review, off by default.** With `NAVIN_SECURITY_REVIEW=1`, the diff of the files changed in the turn is reviewed by the configured model when the agent is about to finish, and every `git commit` run by the agent is reviewed too. Medium, high and critical findings make the agent keep working on them.
- **`/security-review` on demand.** A full review of the uncommitted work, a branch or a commit range.

The `code-reviewer` agent splits large reviews by area, and the `security-auditor` skill covers full audits.

## What the pattern warnings catch

| Family | Examples |
|---|---|
| Code execution | `eval(`, `new Function`, `os.system(`, `subprocess(..., shell=True)`, Node `child_process.exec`, Go `exec.Command("sh", "-c", ...)` |
| Deserialization | `pickle.load`, `cPickle`, `dill`, `marshal`, `shelve`, `joblib.load`, `pd.read_pickle`, `yaml.load`, `torch.load` without `weights_only=True` |
| Browser XSS | `innerHTML =`, `outerHTML =`, `insertAdjacentHTML(`, `document.write`, `dangerouslySetInnerHTML`, external `<script src>` without `integrity` |
| Crypto and TLS | `verify=False`, `rejectUnauthorized: false`, `InsecureSkipVerify: true`, `NODE_TLS_REJECT_UNAUTHORIZED=0`, ECB mode, `crypto.createCipher` |
| Parsing | standard library XML parsers (XXE), use `defusedxml` |
| CI | any edit to `.github/workflows/*.yml`: untrusted event fields in `run:` steps |

## Example: a warning in action

The agent writes:

```python
import yaml

config = yaml.load(open("settings.yml"))
```

The tool result it reads then ends with:

```
[security-guidance: automated reminder, not a user message]

settings.py (unsafe_yaml_load):
Security reminder: yaml.load() and yaml.unsafe_load() can build arbitrary Python objects
through !!python/object tags. Use yaml.safe_load() and validate the result against a schema.
```

You see the notice `Security reminder: unsafe_yaml_load in settings.py`, and the agent switches to `yaml.safe_load`.

## Example: turning on the AI review

```bash
export NAVIN_SECURITY_REVIEW=1
# optional: a cheaper model for reviews
export NAVIN_SECURITY_REVIEW_MODEL=gpt-4.1-mini
```

When the agent is about to finish a turn that changed `api/users.py`, the review answers for example:

```
Security review of this turn's changes found potential vulnerabilities.
Files: api/users.py
api/users.py:
  1. [HIGH] [SQL injection] cursor.execute("select ... '" + q + "'")
     Risk: the q query parameter reaches the SQL string unescaped.
     Fix: cursor.execute("select ... %s", (q,))
```

The agent fixes it (or explains why it does not apply) before ending, at most once per turn. The same file and category are not raised twice in a session.

## Example: on-demand review

```
/security-review
/security-review main
/security-review a1b2c3..HEAD
```

The report is a table (severity, category, file and line, attack in one line) followed by the vulnerable code, the exploit and the fix for each finding. Nothing is edited until you ask.

## Project configuration

All files are optional, read from `~/.navin/` then `<workspace>/.navin/`, then the `.local` variant.

`security-guidance.md`: policy added to the AI review prompt (8 KB cap). It can add checks or describe approved helpers, never suppress a finding.

```markdown
- Database access goes through db.query(sql, params); any other execute() is a finding.
- Treat every /internal/* route as reachable from the internet.
```

`security-patterns.yaml` (or `.yml`, `.json`): extra pattern warnings, up to 50.

```yaml
patterns:
  - rule_name: no-md5
    reminder: MD5 is broken for security use. Use hashlib.sha256 or a password hasher.
    regex: hashlib\.md5
    paths: ["*.py"]
  - rule_name: no-raw-sql-helper
    reminder: Use db.query(sql, params) instead of raw_sql().
    substrings: ["raw_sql("]
    exclude_paths: ["tests/*"]
```

Regexes with catastrophic backtracking shapes such as `(a+)+` are skipped.

## Switches

| Variable | Default | Effect |
|---|---|---|
| `NAVIN_SECURITY_GUIDANCE` | `1` | `0` turns every layer off |
| `NAVIN_SECURITY_PATTERNS` | `1` | `0` turns the pattern warnings off |
| `NAVIN_SECURITY_REVIEW` | `0` | `1` turns on the AI review at end of turn and after commits |
| `NAVIN_SECURITY_REVIEW_MODEL` | configured model | model used for the AI review |

The system prompt mentions the layers only when project patterns are defined or the AI review is on; the default pattern warnings arrive with the tool result that triggers them.
