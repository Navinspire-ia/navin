# Evaluating an MCP server

The quality of a server is not the number of tools. It is whether a model that
has only the tool list, with no other context, can answer realistic questions
correctly. An evaluation set makes that measurable and repeatable.

## Shape of the set

- 10 or more questions (the example file ships eleven).
- Each question is answerable with the server alone, through read-only,
  idempotent calls. Nothing in the set creates, updates or deletes.
- Questions are independent: none relies on a previous one.
- Each question needs several tool calls: a search, then a read, then a
  comparison. One-call lookups test nothing.
- Each question has one expected answer that can be compared as a string:
  a name, a number, a date in a stated format, True or False, a title.
- The answer is stable over time: it refers to closed projects, past
  periods, fixed documents. Never counts of things that still change.

## File format

JSON (preferred):

```json
{
  "server": "notes_mcp",
  "questions": [
    {
      "question": "Which folder holds the earliest note mentioning the vendor renewal? Give the folder path.",
      "answer": "finance",
      "tools": ["notes_search", "notes_get"]
    }
  ]
}
```

`tools` is optional and lists the tools a competent solver would use; the
script reports any that the server does not expose.

XML is accepted for compatibility:

```xml
<evaluation>
  <qa_pair>
    <question>...</question>
    <answer>...</answer>
  </qa_pair>
</evaluation>
```

Validate with:

```bash
python3 navin/skills/mcp-builder/scripts/evaluate.py --validate-only evaluation.json
```

## Writing the questions

1. Inspect the tools as the model would: run `connections.py list-tools` and
   read names, descriptions and schemas. Do not read the server source at
   this stage; the point is to see what the descriptions alone convey.
2. Explore the data with read-only calls and small limits (`limit` under 10)
   to find concrete anchors: a closed project, a past incident, a document
   with a known date.
3. Draft questions that:
   - start from a paraphrase, not a keyword that appears verbatim in the
     data ("the project named after a bird of prey", not "project Falcon");
   - require at least two hops (find the thing, then read a detail of it);
   - exercise different data types across the set: names, dates, counts,
     booleans, titles, ids;
   - fix the output format in the question when several are possible
     ("formatted YYYY-MM-DD", "answer True or False", "the title exactly as
     stored").
4. Solve each question with the tools and record the answer. A question that
   cannot be solved with the current tools is still useful: it points at a
   missing tool or a description that hides a capability. Keep it and note it.
5. Add two or three deliberately ambiguous questions where the solver must
   choose between tools; make sure the answer is still unique.

Weak questions to avoid: "how many notes are there" (changes over time),
"list all tags" (a list is hard to compare), "what is note 42 about" (one
call, free text answer).

## Running the evaluation

The bundled script does not call an LLM. It prepares the ground:

```bash
python3 navin/skills/mcp-builder/scripts/evaluate.py evaluation.json \
  --transport stdio --command python3 --arg server.py --output eval-report.md
```

It validates the file, connects, lists the tools, flags tools referenced by
questions but absent from the server, and writes a report with one row per
question. Then run the questions:

- In Navin, `spawn` one subagent per question (or per batch of three) with a
  task containing only: the tool list as JSON, the question, and the
  instruction to answer using the tools only and to end with the bare answer
  on its own line plus the sequence of tool calls made. The subagent must
  not see the server code or the expected answer.
- Or drive the model manually from the chat with the same constraints.

Fill `actual`, `tool_calls` and `verdict` in the report.

## Grading rubric

| Verdict | Meaning |
|---|---|
| pass | actual equals expected after trimming and case folding |
| partial | right object, wrong format (date style, extra words) |
| fail | wrong answer or NOT_FOUND |

Record alongside each row: number of tool calls, any tool error seen, any
response that was too large to use, and whether the solver hesitated between
tools. These observations are the real output; the pass rate is the summary.

Target: 8 of 10 pass on the first full run after fixes, with no question
failing because of a description or schema problem.

## Iteration loop

For every partial or fail, find the cause in the server, not in the question:

| Symptom | Likely fix |
|---|---|
| Solver picked a sibling tool | sharpen both descriptions, say when to use the other |
| Solver omitted a required argument | make it required in the schema and name it in the description |
| Solver stopped after one page | end markdown pages with the next-offset hint |
| Response too large | lower the default limit, trim metadata in markdown mode |
| Solver mis-parsed dates or ids | render human dates, show names with ids in parentheses |
| Error text did not help | rewrite the error with the next step |
| Solver could not do it at all | a tool is missing; add it |

Change the server, rebuild, rerun `list-tools` to confirm the descriptions,
and run the failing questions again. Keep the evaluation file in the server
repository and extend it when a new tool lands.
