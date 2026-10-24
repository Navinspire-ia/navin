---
name: require-tests-run
enabled: false
event: stop
action: block
conditions:
  - field: transcript
    operator: not_regex_match
    pattern: npm test|npm run test|pytest|cargo test|go test|test_run
---

**No test run found in this turn**

Before finishing, run the tests that cover the change, for example:
- `npm test`
- `pytest`
- `cargo test`

This rule refuses to stop while no test command appears in the turn. Enable it only when strict test enforcement is wanted.
