You are writing a handover note to yourself. Older turns of this conversation are being dropped to free context, and this note is all you will keep of them.

Write it for whoever picks the work up mid-flight with no memory of how it got here. The question it must answer is "what was I doing, how far did I get, and what is the next move" - not "what did I learn about this user".

Cover, and only when the conversation actually establishes it:

- **Goal**: what the user asked for, in their terms, including constraints they insisted on.
- **Task**: the board task being worked on (its id and title) when the conversation names one.
- **Done**: changes already applied, with file paths. Name them; "refactored the auth layer" is useless without the files.
- **Verified**: what was actually run and what it said - tests, lint, a command whose output mattered. Distinguish "tests pass" from "not run yet".
- **Decisions**: choices made and the reason, especially where an obvious alternative was rejected. This is what stops the work from being redone or reversed.
- **State**: anything half-finished - the exact file and symbol being edited, the command about to run, the error being chased - plus what is known about the codebase that was expensive to discover and would cost another search to find again.
- **Next**: the immediate next step as a concrete action (a command to run, a file to edit), and any blocker or open question.

Rules:

- Be specific and short. Exact paths, symbols, commands and error text beat prose.
- Carry over corrections the user made. A repeated mistake is the worst outcome of compaction.
- Record failed attempts and why they failed, so they are not tried again.
- Never invent progress. If something was planned but not done, say it was planned.
- Omit any section the conversation does not support rather than padding it.
- No preamble, no closing remark.

If the chunk contains no work worth carrying, output: (nothing)
