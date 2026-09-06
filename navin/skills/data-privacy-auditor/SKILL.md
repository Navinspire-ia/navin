---
name: data-privacy-auditor
description: Audit data protection and privacy - PII/PHI handling, encryption at rest and in transit, retention, logging of sensitive data, tenant isolation, and GDPR/CCPA obligations. Use for /vault, privacy reviews, or compliance-driven data audits.
metadata: {"navin":{"emoji":"🔒","category":"security"}}
---

# Data Protection & Privacy Auditor

## Overview

Follow the sensitive data, not just the code. Identify what personal or confidential data the system collects, where it flows, how it is protected, and whether that matches legal and contractual obligations. Cite the model/table/field and the file that handles it.

## What to inspect

1. **Data inventory** - classify fields: identifiers (email, phone, national ID), sensitive (health, financial, biometric), secrets, and derived data. Note where each is stored (DB, cache, logs, third parties).
2. **Encryption in transit** - TLS enforced end to end, no plaintext internal hops, no downgrade.
3. **Encryption at rest** - DB/volume/backup encryption, field-level encryption for the most sensitive fields, key management (rotation, not hardcoded).
4. **Logging & telemetry** - PII in application logs, error trackers, analytics, or LLM prompts sent to third-party providers. This is a frequent and serious leak.
5. **Retention & deletion** - data kept beyond need, no deletion path, soft-deletes that never purge, backups that ignore erasure requests.
6. **Access & minimization** - who/what can read PII, over-broad queries (`SELECT *`), export endpoints, and whether collection is minimized.
7. **Tenant isolation** (multi-tenant) - every query scoped by tenant; no cross-tenant leakage via IDs, caches, or search indexes.
8. **Third parties & transfers** - sub-processors, cross-border transfers, and what PII leaves the system (including to AI model providers).

## Regulatory mapping

Map findings to obligations where relevant: **GDPR** (lawful basis, DSAR/erasure, data minimization, records of processing), **CCPA/CPRA**, **HIPAA** (PHI), **PCI-DSS** (cardholder data). State the gap, not just the principle.

## Deliverable

- A data-flow + classification table.
- Findings rated by sensitivity × exposure, each with the fix (encrypt, redact log, scope query, add deletion path).
- The privacy gaps that block a given regulation, ordered by risk.

## Anti-patterns

- Auditing code paths while ignoring logs, backups, analytics, and AI-provider calls
- Quoting regulations abstractly without tying them to a concrete field or flow
- Treating soft-delete as erasure
