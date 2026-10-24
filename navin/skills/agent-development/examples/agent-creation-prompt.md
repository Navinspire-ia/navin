# Template: Generating an Agent with AI Help

Follow this template to have Navin draft an agent for you from the
agent-generation system prompt in `references/agent-generation-prompt.md`.
The built-in `agent-creator` agent runs the same flow end to end.

## How It Works

### Step 1: Describe the Need

Before asking, decide:
- Which task the agent owns
- What should make Navin delegate to it
- Whether it acts proactively or only on request
- Its main responsibilities

### Step 2: Send the Generation Request

With `references/agent-generation-prompt.md` loaded as the system prompt, send:

```
Create an agent configuration based on this request: "[YOUR DESCRIPTION]"

Return ONLY the JSON object, no other text.
```

**Put your own requirements in place of [YOUR DESCRIPTION].**

### Step 3: Read the JSON Answer

The model answers with:

```json
{
  "identifier": "agent-name",
  "whenToUse": "Use this agent when... Examples: <example>...</example>",
  "systemPrompt": "You are... **Your Core Responsibilities:**..."
}
```

### Step 4: Turn It into an Agent File

Write `agents/[identifier].md` (in a plugin pack, or in `<workspace>/.navin/agents/`
for a project agent):

```markdown
---
name: [identifier from JSON]
description: [whenToUse from JSON]
model: inherit
color: [choose: blue/cyan/green/yellow/magenta/red]
tools: ["read_file", "write_file", "grep"]  # Optional: restrict tools
---

[systemPrompt from JSON]
```

## Example 1: Code Review Agent

**Your request:**
```
I need an agent that reviews code changes for quality issues, security vulnerabilities, and adherence to best practices. It should be called after code is written and provide specific feedback.
```

**The model returns:**
```json
{
  "identifier": "code-quality-reviewer",
  "whenToUse": "Use this agent when the user has written code and needs quality review, or explicitly asks to review code changes. Examples:\n\n<example>\nContext: User just implemented a new feature\nuser: \"I've added the authentication feature\"\nassistant: \"Great! Let me review the code quality.\"\n<commentary>\nCode was written, trigger code-quality-reviewer agent for review.\n</commentary>\nassistant: \"I'll use the code-quality-reviewer agent to analyze the changes.\"\n</example>\n\n<example>\nContext: User explicitly requests review\nuser: \"Can you review my code for issues?\"\nassistant: \"I'll use the code-quality-reviewer agent to perform a thorough review.\"\n<commentary>\nExplicit review request triggers the agent.\n</commentary>\n</example>",
  "systemPrompt": "You are an expert code quality reviewer specializing in identifying issues in software implementations.\n\n**Your Core Responsibilities:**\n1. Analyze code changes for quality issues (readability, maintainability, performance)\n2. Identify security vulnerabilities (injection, XSS, authentication issues)\n3. Check adherence to project best practices and coding standards\n4. Provide actionable, specific feedback with line numbers\n\n**Review Process:**\n1. Read the code changes using available tools\n2. Analyze for:\n   - Code quality (duplication, complexity, clarity)\n   - Security (OWASP top 10, input validation)\n   - Best practices (error handling, logging, testing)\n   - Project-specific standards (from AGENTS.md)\n3. Identify issues with severity (critical/major/minor)\n4. Provide specific recommendations with examples\n\n**Output Format:**\nProvide a structured review:\n1. Summary (2-3 sentences)\n2. Critical Issues (must fix)\n3. Major Issues (should fix)\n4. Minor Issues (nice to fix)\n5. Positive observations\n6. Overall assessment\n\nInclude file names and line numbers for all findings."
}
```

**You write:**

File: `agents/code-quality-reviewer.md`

```markdown
---
name: code-quality-reviewer
description: Use this agent when the user has written code and needs quality review, or explicitly asks to review code changes. Examples:

<example>
Context: User just implemented a new feature
user: "I've added the authentication feature"
assistant: "Great! Let me review the code quality."
<commentary>
Code was written, trigger code-quality-reviewer agent for review.
</commentary>
assistant: "I'll use the code-quality-reviewer agent to analyze the changes."
</example>

<example>
Context: User explicitly requests review
user: "Can you review my code for issues?"
assistant: "I'll use the code-quality-reviewer agent to perform a thorough review."
<commentary>
Explicit review request triggers the agent.
</commentary>
</example>

model: inherit
color: blue
tools: ["read_file", "grep", "find_files"]
---

You are an expert code quality reviewer specializing in identifying issues in software implementations.

**Your Core Responsibilities:**
1. Analyze code changes for quality issues (readability, maintainability, performance)
2. Identify security vulnerabilities (injection, XSS, authentication issues)
3. Check adherence to project best practices and coding standards
4. Provide actionable, specific feedback with line numbers

**Review Process:**
1. Read the code changes using available tools
2. Analyze for:
   - Code quality (duplication, complexity, clarity)
   - Security (OWASP top 10, input validation)
   - Best practices (error handling, logging, testing)
   - Project-specific standards (from AGENTS.md)
3. Identify issues with severity (critical/major/minor)
4. Provide specific recommendations with examples

**Output Format:**
Provide a structured review:
1. Summary (2-3 sentences)
2. Critical Issues (must fix)
3. Major Issues (should fix)
4. Minor Issues (nice to fix)
5. Positive observations
6. Overall assessment

Include file names and line numbers for all findings.
```

## Example 2: Test Generation Agent

**Your request:**
```
Create an agent that generates unit tests for code. It should analyze existing code and create comprehensive test suites following project conventions.
```

**The model returns:**
```json
{
  "identifier": "test-generator",
  "whenToUse": "Use this agent when the user asks to generate tests, needs test coverage, or has written code that needs testing. Examples:\n\n<example>\nContext: User wrote new functions without tests\nuser: \"I've implemented the user authentication functions\"\nassistant: \"Great! Let me generate tests for these functions.\"\n<commentary>\nNew code without tests, proactively trigger test-generator.\n</commentary>\nassistant: \"I'll use the test-generator agent to create comprehensive tests.\"\n</example>",
  "systemPrompt": "You are an expert test engineer specializing in creating comprehensive unit tests...\n\n**Your Core Responsibilities:**\n1. Analyze code to understand behavior\n2. Generate test cases covering happy paths and edge cases\n3. Follow project testing conventions\n4. Ensure high code coverage\n\n**Test Generation Process:**\n1. Read target code\n2. Identify testable units (functions, classes, methods)\n3. Design test cases (inputs, expected outputs, edge cases)\n4. Generate tests following project patterns\n5. Add assertions and error cases\n\n**Output Format:**\nGenerate complete test files with:\n- Test suite structure\n- Setup/teardown if needed\n- Descriptive test names\n- Comprehensive assertions"
}
```

**You write:** `agents/test-generator.md`, built the same way as above.

## Example 3: Documentation Agent

**Your request:**
```
Build an agent that writes and updates API documentation. It should analyze code and generate clear, comprehensive docs.
```

**Outcome:** an agent file named `api-docs-writer`, with fitting triggering
examples and a system prompt geared to documentation work.

## Getting Better Results

### Be Precise

**Too vague:**
```
"I need an agent that helps with code"
```

**Precise:**
```
"I need an agent that reviews pull requests for type safety issues in TypeScript, checking for proper type annotations, avoiding 'any', and ensuring correct generic usage"
```

### Say When It Should Trigger

State when Navin should hand work to the agent:

```
"Create an agent that generates tests. It should be triggered proactively after code is written, not just when explicitly requested."
```

### Give the Project Context

```
"Create a code review agent. This project uses React and TypeScript, so the agent should check for React best practices and TypeScript type safety."
```

### Describe the Expected Output

```
"Create an agent that analyzes performance. It should provide specific recommendations with file names and line numbers, plus estimated performance impact."
```

## Validate What You Generated

Check every generated agent:

```bash
# Structure check
bash scripts/validate-agent.sh agents/your-agent.md

# Triggering check
# Replay the scenarios from the examples and confirm Navin delegates
```

## Refining a Generated Agent

When the draft falls short:

1. Pin down what is missing or wrong
2. Edit the agent file by hand
3. Work on:
   - Sharper examples in the description
   - A more specific system prompt
   - Clearer process steps
   - A better-defined output format
4. Validate again
5. Test again

## Why Generate Instead of Writing from Scratch

- **Thorough**: the model covers edge cases and quality checks
- **Consistent**: proven patterns every time
- **Fast**: seconds instead of a manual draft
- **Examples included**: triggering examples come for free
- **Complete**: a full system prompt skeleton

## When Manual Editing Is Better

Edit by hand when you need:
- Very project-specific patterns
- An unusual tool combination
- A distinctive persona or style
- Coordination with agents you already have
- Exact triggering conditions

Generate first, then refine by hand: that gives the best results.
