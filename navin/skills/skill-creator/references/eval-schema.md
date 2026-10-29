# Evaluation schemas for skill-creator

All files are JSON. Paths are relative to the folder named on the command line.

## evals.json

```json
{
  "skill": "pdf",
  "evals": [
    {
      "id": "fill-form",
      "prompt": "Fill the attached registration form with the values in values.json and give me the result.",
      "files": ["fixtures/form.pdf", "fixtures/values.json"],
      "assertions": [
        {"id": "a1", "type": "file_exists", "path": "outputs/filled.pdf", "description": "a filled PDF is produced"},
        {"id": "a2", "type": "contains", "target": "transcript", "value": "pdf_forms.py", "description": "the skill script was used"},
        {"id": "a3", "type": "regex", "target": "transcript", "pattern": "(?i)flatten", "negate": true, "description": "no flattening unless asked"},
        {"id": "a4", "type": "json_path", "path": "outputs/report.json", "json_path": "filled.0", "equals": "Name"}
      ]
    },
    {
      "id": "near-miss",
      "prompt": "Write a short poem about autumn.",
      "assertions": [
        {"type": "contains", "value": "pdf_", "negate": true, "description": "the skill must not be used"}
      ]
    }
  ]
}
```

| Field | Required | Meaning |
|-------|----------|---------|
| `evals[].id` | no | Slug used for folder names; defaults to the position |
| `evals[].prompt` | yes | The user request, written as a real user would |
| `evals[].files` | no | Input files staged next to the prompt (listed in the prompt) |
| `evals[].assertions` | no | Deterministic checks graded by `run_eval.py --results` |

### Assertion types

| `type` | Fields | Passes when |
|--------|--------|-------------|
| `file_exists` | `path` | the file exists under the result folder |
| `contains` | `target` (`transcript` or a relative path), `value`, `case_insensitive` | the text contains `value` |
| `regex` | `target`, `pattern`, `case_insensitive` | the pattern matches (multiline) |
| `json_path` | `path`, `json_path` (dotted, numeric tokens index lists), optional `equals` | the key exists, and equals the value when given |

Every assertion accepts `negate: true`, `id` and `description`.

## Run folder (written by run_eval.py)

```
<run-dir>/run.json                                skill, evals file, timestamp
<run-dir>/cases/<case-id>/case.json               prompt, assertions, configurations
<run-dir>/cases/<case-id>/with_skill/prompt.md     skill body + task
<run-dir>/cases/<case-id>/without_skill/prompt.md  task only
<run-dir>/cases/<case-id>/<config>/grading.json    after --results
<run-dir>/grading.json                            run-level summary
```

## Results folder (produced by the agent runs)

```
<results>/<case-id>/<config>/transcript.md   what the model wrote (output.md also accepted)
<results>/<case-id>/<config>/outputs/...     files it produced
```

`<config>` is `with_skill` or `without_skill`.

## grading.json (run level)

```json
{
  "run_id": "2026-10-29",
  "skill_name": "pdf",
  "graded_at": "2026-10-29T10:00:00Z",
  "configurations": {
    "with_skill": {
      "summary": {"cases": 2, "cases_fully_passed": 2, "passed": 5, "failed": 0, "total": 5, "pass_rate": 1.0},
      "cases": [{"case_id": "fill-form", "configuration": "with_skill", "summary": {"passed": 4, "failed": 0, "total": 4, "pass_rate": 1.0}, "assertions": ["..."]}]
    },
    "without_skill": {"summary": {"cases": 2, "cases_fully_passed": 1, "passed": 2, "failed": 3, "total": 5, "pass_rate": 0.4}, "cases": ["..."]}
  }
}
```

## benchmark.json (aggregate_benchmark.py)

```json
{
  "generated_at": "2026-10-29T10:05:00Z",
  "skill_name": "pdf",
  "runs": [{"run_id": "2026-10-29", "path": "runs/2026-10-29/grading.json"}],
  "configurations": {
    "with_skill": {
      "pass_rate": {"mean": 0.93, "min": 0.9, "max": 1.0, "n": 3},
      "cases": {"fill-form": {"runs": 3, "full_passes": 3, "stability": 1.0, "pass_rate": {"mean": 1.0, "min": 1.0, "max": 1.0, "n": 3}}}
    },
    "without_skill": {"pass_rate": {"mean": 0.41, "min": 0.4, "max": 0.45, "n": 3}, "cases": {"...": {}}}
  },
  "delta": {"primary": "with_skill", "baseline": "without_skill", "pass_rate": 0.52, "per_case": {"fill-form": 0.75}}
}
```

`benchmark.md` is the same content as a table, ready to paste in a report.

## triggers.json (improve_description.py)

```json
[
  {"query": "fill this PDF form for me", "should_trigger": true},
  {"query": "merge these three PDFs", "should_trigger": true},
  {"query": "write a cover letter", "should_trigger": false}
]
```

Aim for 6 to 12 positives covering every trigger phrase of the description and 4 to 8 negatives that look close but belong to other skills.
