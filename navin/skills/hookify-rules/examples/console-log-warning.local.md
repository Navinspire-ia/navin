---
name: warn-console-log
enabled: true
event: file
pattern: console\.log\(
action: warn
---

**console.log detected**

A console.log statement is being added. Consider:
- Is it temporary debugging, or should it be real logging?
- Will it ship to production?
- Should the project logger be used instead?
