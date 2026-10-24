# Parsing settings frontmatter

## The structure

```
---              <- line 1, exactly three dashes
key: value       <- YAML
---              <- closing line
body...          <- Markdown, free text
```

## Python (recommended)

Python 3 is always available where Navin runs, and PyYAML usually is.

```bash
value=$(python3 - "$file" enabled <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
m = re.match(r"^---\s*\n(.*?)\n---\s*(\n|$)", text, re.S)
meta = {}
if m:
    try:
        import yaml
        meta = yaml.safe_load(m.group(1)) or {}
    except Exception:
        for line in m.group(1).splitlines():
            k, sep, v = line.partition(":")
            if sep and not line.startswith((" ", "\t")):
                meta[k.strip()] = v.strip().strip('"').strip("'")
v = meta.get(sys.argv[2], "")
print(str(v).lower() if isinstance(v, bool) else v)
PY
)
```

`scripts/parse-frontmatter.sh` wraps this logic with lists, missing keys and
body extraction handled.

## sed / awk (no Python)

Extract the frontmatter block:

```bash
front=$(awk 'NR==1 && $0!="---"{exit} NR>1 && $0=="---"{exit} NR>1{print}' "$file")
```

Read one scalar key:

```bash
enabled=$(printf '%s\n' "$front" | sed -n 's/^enabled:[[:space:]]*//p' | tr -d '"' | tr -d "'")
```

Extract the body:

```bash
body=$(awk 'f==2{print; next} $0=="---"{f++}' "$file")
```

Limits: no nested values, no multi-line strings, inline comments stay in the
value. Use Python for anything beyond flat scalars.

## Edge cases

| Case | Handling |
|---|---|
| file missing | defaults; never an error |
| no frontmatter | all keys default; body is the whole file |
| unclosed `---` | treat as invalid, log once, use defaults |
| key missing | default for that key |
| `yes`/`no`, `on`/`off` | YAML reads them as booleans; prefer `true`/`false` |
| CRLF line endings | strip `\r` before comparing |
| value with `:` | must be quoted in YAML |

## Performance

Hooks on tool events run often. Exit before parsing when the file is missing,
and parse once per run into variables instead of calling the parser per key
(`parse-frontmatter.sh <file>` prints every key as `key=value`).
