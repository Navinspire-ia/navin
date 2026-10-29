# Incident update example

Format: the same skeleton at every stage so readers can scan for what changed.
Status, impact, what is known, workaround, next update time. No root-cause
speculation before confirmation, no individual names, no blame. Times in one
stated time zone. All content is fictional.

## Post 1 - Investigating (10:12 CET)

```
[Investigating] Studio file preview failing for some workspaces
Impact: Since about 09:55 CET, opening a file preview returns a blank panel
for an estimated 15 percent of workspaces (EU region). Desks, chat and
exports are not affected.
What we know: Error rate on the preview service rose sharply at 09:54. We
are investigating a deployment that finished at 09:50.
Workaround: Download the file from the explorer and open it locally.
Next update: 10:45 CET or sooner if the situation changes.
```

## Post 2 - Identified (10:41 CET)

```
[Identified] Studio file preview failing for some workspaces
Impact: Unchanged, about 15 percent of EU workspaces, preview only.
What we know: The 09:50 deployment introduced a configuration change that
breaks preview rendering for workspaces with a custom storage region. We are
rolling the deployment back.
Workaround: Unchanged, download and open locally.
Next update: 11:15 CET.
```

## Post 3 - Monitoring (11:03 CET)

```
[Monitoring] Studio file preview restored
Impact: Preview is working again for all workspaces as of 10:58 CET. No data
was lost; files uploaded during the incident are intact.
What we know: The rollback completed at 10:57. Error rate is back to the
normal baseline. We are watching for 60 minutes before closing.
Workaround: No longer needed.
Next update: 12:00 CET, or the resolution notice.
```

## Post 4 - Resolved (12:02 CET)

```
[Resolved] Studio file preview restored
Duration: 09:54 to 10:58 CET (64 minutes), about 15 percent of EU workspaces.
Cause: A configuration change shipped at 09:50 did not account for custom
storage regions. The fix will ship with a regression test on Thursday.
Follow-up: An incident review is scheduled for Friday; the written review
will be posted here within five working days. Thank you to the customers who
reported the issue quickly.
```

Notes for the writer:

- Keep the same subject line across posts, changing only the status tag.
- Give a next-update time in every post except the last, and honour it even if
  the update is "no change".
- Impact is described from the reader's seat: which feature, which share of
  users, since when.
- Resolution post states duration, cause in one plain sentence, and the
  follow-up commitment with a date.
- For internal channels, add the incident channel or ticket link; for
  customer-facing status pages, remove internal references.
