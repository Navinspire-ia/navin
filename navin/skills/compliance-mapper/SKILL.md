---
name: compliance-mapper
description: Map the codebase and controls against security standards - OWASP ASVS, CIS Benchmarks, SOC 2, ISO 27001, PCI-DSS - producing a gap analysis with evidence and remediation. Use for /comply, audit prep, or "are we compliant with X?" questions.
metadata: {"navin":{"emoji":"📋","category":"security"}}
---

# Compliance Mapper

## Overview

Translate security findings into the language of a standard. For a chosen framework, go control by control, mark each **met / partial / gap / not-applicable** with concrete evidence from the codebase, and give the work needed to close each gap. This is a readiness assessment, not a certification.

## Supported frameworks

- **OWASP ASVS** - application-level verification (L1/L2), best default for a codebase.
- **CIS Benchmarks** - OS/container/cloud hardening baselines.
- **SOC 2** (Trust Services Criteria) - security, availability, confidentiality controls.
- **ISO 27001 Annex A** - ISMS control set.
- **PCI-DSS** - if cardholder data is handled.
- **GDPR** - pair with the data-privacy-auditor for data-protection articles.

## Workflow

1. Confirm the target framework and scope (whole product, a service, or infra). If unclear, default to OWASP ASVS L2.
2. Pull evidence from prior audits (`/fortify`, `/probe`, `/perimeter`, `/bastion`, `/vault`) and the code itself - do not re-audit from scratch; synthesize.
3. For each control: `ID | requirement | status | evidence (file/config) | gap | remediation | effort`.
4. Roll up a compliance scorecard per domain (authentication, access control, crypto, logging, config, data protection…).
5. Produce a prioritized remediation roadmap: quick wins first, then structural gaps, with an owner suggestion per item.
6. Save the report as a file in the workspace when substantial.

## Anti-patterns

- Marking a control "met" without pointing to concrete evidence
- Claiming the system is "certified/compliant" - this is a gap assessment only
- Copying the full standard text instead of mapping it to this system
- Ignoring not-applicable controls (mark and justify them)
