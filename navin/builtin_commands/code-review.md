---
description: Review a pull request with parallel agents and post only high-signal findings
argument-hint: "[PR number or URL] [--comment]"
allowed-tools: spawn, forge, pr_comments, code_review, git, read_file, grep, find_files, board, Bash(gh pr view:*), Bash(gh pr diff:*), Bash(gh pr list:*), Bash(gh issue view:*), Bash(gh issue list:*), Bash(gh search:*)
---

Review the pull request given in the arguments (or the pull request of the current branch when none is given).

Arguments: $ARGUMENTS

**Rules for you and for every subagent you start (tell them):**
- The tools work. Do not test them and do not make exploratory calls.
- Call a tool only when the task needs it; every call has a clear purpose.

Follow these steps exactly:

1. Start a light subagent (`spawn`; a fast model preset when one is configured) to check whether any of these is true:
   - the pull request is closed;
   - the pull request is a draft;
   - the pull request needs no review (automated change, trivial change that is obviously correct);
   - a Navin review is already on the pull request (a comment starting with `## Code review`).

   If one is true, stop here. Pull requests written by Navin are still reviewed.

2. Start a light subagent to return the paths (not the content) of the guideline files that apply:
   - the root `AGENTS.md`, if there is one, and the other agent rule files the repository keeps;
   - the `AGENTS.md` files in the directories that hold files changed by the pull request.

3. Start a subagent to read the pull request (`forge action=pr_view`, its diff) and return a summary of the change.

4. Start 4 subagents in parallel, in the same reply, to review the change independently. Each returns a list of issues, each with a description and the reason it was flagged (for example "AGENTS.md rule", "bug"):

   Agents 1 and 2: guideline compliance
   Check the change against the guideline files, in parallel. A guideline file only applies to files in its own directory or below it.

   Agent 3: bugs in the diff (strongest model preset available)
   Look for obvious bugs, from the diff alone, without reading extra context. Report only significant bugs; ignore nitpicks and likely false positives. Do not report anything you cannot confirm from the diff.

   Agent 4: problems in the new code (strongest model preset available)
   Look for problems in the code the change introduces: security issues, wrong logic, and so on. Stay within the changed code.

   **CRITICAL: only HIGH-SIGNAL issues.** Report an issue when:
   - the code will not compile or parse (syntax error, type error, missing import, unresolved reference);
   - the code will give a wrong result whatever the input (clear logic error);
   - a guideline rule is clearly broken and you can quote the exact rule.

   Do NOT report:
   - style or quality concerns;
   - potential problems that depend on particular inputs or state;
   - subjective suggestions or improvements.

   When you are not sure an issue is real, leave it out. False positives cost trust and reviewer time.

   Give every subagent the pull request title and description, so it knows the author's intent.

5. For each issue from step 4, start a subagent in parallel to validate it. Give it the pull request title and description and the issue. Its job is to confirm with high confidence that the issue is real: for "variable is not defined", check that it really is undefined in the code; for a guideline issue, check that the rule applies to that file and that it is really broken. Use the strongest model preset for bugs and logic issues, a standard one for guideline issues.

6. Drop every issue that step 5 did not confirm. What remains is the list of high-signal issues.

7. Print a summary of the review in the chat:
   - when there are issues, list each one with a short description;
   - when there are none, write: "No issues found. Checked for bugs and AGENTS.md compliance."

   Without `--comment` in the arguments, stop here and post nothing on the forge.

   With `--comment` and no issue, post the summary comment below (`forge action=pr_review event=comment`) and stop.

   With `--comment` and issues, go on to step 8.

8. Write down, for yourself only, every comment you plan to leave, to make sure you stand behind each one. Do not post this list.

9. Post one inline comment per issue with `pr_comments action=post` (`findings_json` entries with `file_path`, `line` and the comment text). For each comment:
   - describe the issue briefly;
   - for a small, self-contained fix, add a suggestion block that can be committed as is;
   - for a larger fix (6 lines or more, structural changes, or several places), describe the issue and the fix without a suggestion block;
   - never post a suggestion block unless committing it fixes the issue completely. If more steps are needed, leave the suggestion out.

   **IMPORTANT: one comment per distinct issue. Never post duplicates.**

These are false positives in steps 4 and 5 (do NOT report them):

- issues that existed before the pull request;
- code that looks wrong but is correct;
- pedantic nitpicks a senior engineer would not raise;
- what a linter will catch (do not run the linter to check);
- general quality concerns (test coverage, general security posture) unless a guideline file requires them;
- issues a guideline mentions but that the code silences explicitly (for example a lint ignore comment).

Notes:

- Talk to the forge through `forge`, `pr_comments` or the `gh` CLI, never through web fetching.
- Put the steps on the board before you start.
- Cite and link every issue in its inline comment (when it is about a guideline file, link that file).
- When there is no pull request for the branch, review the local change-set from `code_review action=scope` with the same steps and only print the summary.
- With `--comment` and no issue, the comment reads:

---

## Code review

No issues found. Checked for bugs and AGENTS.md compliance.

---

- Links to code in comments must follow this exact format, otherwise the Markdown preview does not render them: `https://github.com/<owner>/<repo>/blob/<full-commit-sha>/path/to/file.ext#L4-L7`
  - the full commit sha is required: write the sha itself, never a `$(git rev-parse HEAD)` expression, because the comment is rendered as is;
  - the repository must be the one under review;
  - a `#` follows the file name;
  - the line range is written `L<start>-L<end>`;
  - include at least 1 line of context before and after, centred on the commented lines (comment on lines 5-6, link `L4-L7`).
