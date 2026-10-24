# Issue Triage Plugin

Two commands that keep an issue tracker tidy: spot duplicates, and label issues correctly and conservatively.

## Overview

The `issue-triage` plugin gives Navin two maintenance commands for the issues of a repository on GitHub (or another forge Navin can reach):

- **`/dedupe`** - finds up to 3 likely duplicates of an issue and leaves one comment listing them;
- **`/triage-issue`** - analyses an issue and adds or removes labels, using only labels that already exist, and never comments.

For triaging a whole backlog at once, the plugin points to the `issue-triage` skill; the `github` skill covers other forge operations.

## Commands

### `/dedupe`

**Arguments:** `<issue number or URL>`

**How it works:**

1. **Pre-check** (subagent): stops if the issue is closed, does not need deduplication (broad feedback with no specific request, praise), or already has a duplicates comment posted earlier by Navin.
2. **Summary** (subagent): reads the issue and summarises it.
3. **Search**: 5 subagents in parallel search the repository's issues from that summary, each with a different strategy and different keywords.
4. **Filtering**: one more subagent drops the false positives, keeping only real duplicate candidates. If none is left, Navin stops.
5. **Comment**: after checking that the original and each candidate exist (3 at most), Navin posts one comment with `forge action=issue_comment`.

**Usage:**

```
/dedupe 318
/dedupe https://github.com/acme/shop/issues/318
```

**Sample comment:**

```
Found 2 possible duplicate issue(s):

1. https://github.com/acme/shop/issues/204
2. https://github.com/acme/shop/issues/291

- If your issue is a duplicate, close it and add a reaction to the existing issue instead.
- If it is not, comment here so it stays open.
```

**Highlights:**

- at most 3 candidates, after a dedicated false-positive pass;
- never posts twice on the same issue;
- reads the forge only through `forge` or the `gh` CLI (`gh issue view`, `gh issue list`, `gh search issues`), never through web fetching, and edits no files.

### `/triage-issue`

**Arguments:** `<issue number> [event: issues|issue_comment]`. The event defaults to `issues` (a new issue); pass `issue_comment` when a comment was added to an existing issue.

**Golden rule:** Navin only adds and removes labels. It never posts a comment or message on the issue, and only uses labels returned by `gh label list`.

**How it works:**

1. Fetches the existing labels (`gh label list --limit 100`).
2. Reads the issue (`gh issue view <number>`).
3. Reads the conversation (`gh issue view <number> --comments`).

**On a new issue (`issues`):**

4. **Relevance check.** Looks in the body for signals that tie the issue to this repository's product (version output, commands, configuration files, integrations, logs or paths). If any signal is present, the issue is about the product. Only when none is present and another product is clearly the subject of the complaint does it apply `invalid` and stop. When in doubt, it does not apply `invalid`. The body wins over form fields that contradict it.
5. **Category labels.** Type (`bug`, `enhancement`, `question`, ...), technical areas and platform, plus a duplicate search with `gh search issues` (only open issues count as duplicate targets). Exactly one of `bug`, `enhancement`, `question`, `invalid` or `duplicate` is always applied; the closest fit beats none.
6. **Lifecycle labels** (bugs only):
   - `needs-repro` (7 days) - no steps someone else could follow. Not applied when the reporter gave error messages, logs, paths or a narrative of what they did; for model-behaviour reports, examples are enough.
   - `needs-info` (7 days) - something is missing from the reporter (errors, versions, environment, answers). Not applied when those are present, or when the issue just needs engineering investigation.

   Issues with these labels are closed automatically when nobody answers in time.
7. Applies everything in one `forge action=issue_labels` call.

**On a new comment (`issue_comment`):**

- removes `stale` or `autoclose`: a new human comment means the issue is alive;
- removes `needs-repro` / `needs-info` when the missing details have arrived ("+1", "me too" and reactions do not count);
- adds a lifecycle label when a maintainer clearly asked for details and none is set;
- never changes category labels; changing nothing is a valid outcome.

**Usage:**

```
/triage-issue 412
/triage-issue 412 event: issue_comment
```

**Sample result (new issue):**

```
Issue #412 "Crash when exporting an empty report"
  added: bug, area:export, needs-repro
  removed: -
Reason: crash described without steps or logs.
```

**Sample result (comment event):**

```
Issue #412 - reporter added a stack trace and steps
  added: -
  removed: needs-repro
```

## Best Practices

- Keep a clean, documented label set: Navin can only use what exists.
- Run `/triage-issue` on every new issue and on new comments, so lifecycle labels stay accurate.
- Run `/dedupe` on new issues before investing time in them.
- Let the conservative rules work: a missing lifecycle label is better than a wrong one.

### When to use

- busy repositories with a steady stream of issues;
- automated triage on issue and comment events;
- cleaning up a backlog with many repeated reports.

### When not to use

- when you want a reply posted to the reporter (`/triage-issue` never comments);
- closed issues, praise or broad feedback (`/dedupe` skips them);
- repositories without a label set (create the labels first).

## Workflow Integration

### New issue

```
/triage-issue 512
/dedupe 512
```

### New comment on an issue

```
/triage-issue 512 event: issue_comment
```

### Automation

```
# on "issue opened":     /triage-issue <n>  then  /dedupe <n>
# on "comment created":  /triage-issue <n> event: issue_comment
```

## Requirements

- A repository on a forge Navin can reach, with a token configured for the `forge` tool (labels and comments).
- The `gh` CLI, installed and authenticated (`gh auth login`), used for reading labels, issues and searches.
- Subagent support (`spawn`) for `/dedupe`.
- For `/triage-issue`, the labels you expect (`bug`, `enhancement`, `question`, `invalid`, `duplicate`, `needs-repro`, `needs-info`, ...) must exist in the repository.

## Troubleshooting

### `/dedupe` posts nothing

**Cause:** the issue was skipped (closed, not deduplicable, already commented) or every candidate was filtered out as a false positive.

**Fix:** this is expected; run the searches yourself with `gh search issues` if you suspect a duplicate.

### `/triage-issue` skips a label you expected

**Cause:** the label does not exist (Navin never invents one), or the conservative rules did not consider it clearly warranted.

**Fix:** check `gh label list`, create the missing label, or add it manually.

### Wrong `invalid` label

**Cause:** the issue body had no visible signal tying it to the product.

**Fix:** remove the label; asking reporters to include the version or logs in issue templates prevents it.

### `gh` errors

**Fix:** run `gh auth status`, then `gh auth login` if needed, and make sure you are inside the right repository.

## Tips

- Put product signals (version, config paths) in your issue templates: they make relevance checks reliable.
- Duplicates are only matched against open issues during triage.
- Each `/dedupe` step is put on the project board.
- Override the commands in your project's `.navin/` folder or in `~/.navin/plugins/issue-triage/` to adapt labels or rules.

## Plugin contents

| Item | Kind |
|---|---|
| `/dedupe`, `/triage-issue` | commands |
| `issue-triage`, `github` | related skills |

## Version

1.0.0
