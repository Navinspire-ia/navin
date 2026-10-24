---
name: block-dangerous-rm
enabled: true
event: bash
pattern: rm\s+-rf
action: block
---

**Dangerous rm command detected**

This command can delete important files. Before running it:
- Check that the path is exactly the one intended
- Prefer a narrower delete or a move to a temporary folder
- Make sure nothing unrecoverable is inside
