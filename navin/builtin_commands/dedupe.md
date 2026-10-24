---
description: Find up to 3 likely duplicates of an issue and comment them on it
argument-hint: "<issue number or URL>"
allowed-tools: spawn, forge, board, Bash(gh issue view:*), Bash(gh issue list:*), Bash(gh search issues:*)
---

Find at most 3 likely duplicates of the issue given in the arguments.

Issue: $ARGUMENTS

Follow these steps exactly:

1. Start a subagent to check whether the issue (a) is closed, (b) does not need deduplication (broad product feedback with no specific request, praise), or (c) already carries a duplicates comment posted earlier by Navin. If so, stop.
2. Start a subagent to read the issue and return a summary of it.
3. Start 5 subagents in parallel, in the same reply, to search the repository's issues for duplicates from that summary. Give each a different strategy and different keywords.
4. Give the summary and the candidates to one more subagent, whose job is to drop the false positives: issues that are probably not real duplicates of the original. When no candidate is left, stop.
5. Check that the original issue and every remaining candidate exist (issue numbers only, 3 at most), then post one comment on the original issue with `forge action=issue_comment`:

   ```
   Found <N> possible duplicate issue(s):

   1. https://github.com/<owner>/<repo>/issues/<number>
   2. ...

   - If your issue is a duplicate, close it and add a reaction to the existing issue instead.
   - If it is not, comment here so it stays open.
   ```

Notes (pass them on to your subagents too):

- Read the forge with the `gh` CLI or `forge`, never with web fetching. Examples:
  - `gh issue view 123` - read an issue
  - `gh issue view 123 --comments` - read it with its comments
  - `gh issue list --state open --limit 20` - list issues
  - `gh search issues "query" --repo <owner>/<repo> --limit 10` - search issues
- Use no other tool beyond those and the comment above (no file edits, no other servers).
- Put the steps on the board first.
