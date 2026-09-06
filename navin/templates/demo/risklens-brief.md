# Demo brief: Local AI launch kit

## Goal

Ship a desktop AI agent that a non-technical founder can install in under
five minutes, run a RiskLens pre-mortem, then generate a one-page GTM plan.

## Constraints

- Must work offline after install (BYOK or local model).
- Setup should complete without requiring a terminal.
- No cloud chat history required for the happy path.
- Budget: solo founder, less than $50/month for managed models.

## Risks to pressure-test

1. Install friction (SmartScreen / Gatekeeper) kills conversion.
2. BYOK setup is too hard for a first-time user.
3. Marketing promises "full cloud sync" while chats stay local.
4. Too many studios dilute the core narrative.

## Success criteria

- Wizard completes in 3 steps.
- RiskLens produces a ranked risk list from this brief.
- User can open Vision 360 on `vision360-mini/` without extra setup.
