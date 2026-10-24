---
name: warn-sensitive-files
enabled: true
event: file
action: warn
conditions:
  - field: file_path
    operator: regex_match
    pattern: \.env$|\.env\.|credentials|secrets
---

**Sensitive file detected**

This file may hold sensitive data:
- Do not hardcode credentials
- Read secrets from environment variables
- Check that the file is listed in .gitignore
- Consider a secrets manager
