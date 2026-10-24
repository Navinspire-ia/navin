[Subagent '{{ label }}' {{ status_text }}]

Task: {{ task }}

Result:
{{ result }}

Summarize this naturally for the user. Keep it brief (1-3 sentences). Do not mention technical details like "subagent" or task IDs.
Always say plainly where the work went:
- If the result says the changes were merged automatically, say so and give the number of files merged.
- If the result asks you to integrate the changes yourself, do it now with your tools before you answer, without asking the user for permission, then say which files you merged.
- If the result says the user can choose Resolve, Keep separate or Discard, say so clearly, name how many files conflict and which ones, and that nothing in their files was changed.
- If the result says the work was kept in a separate checkout for review, say that it is not in their files yet.
