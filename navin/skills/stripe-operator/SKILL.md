---
name: stripe-operator
description: Inspect and operate Stripe (customers, payments, subscriptions, invoices, products) through the Stripe API or CLI. Use for billing questions, payment debugging, and revenue reporting.
metadata: {"navin":{"emoji":"💳","category":"data","requires":{"env":["STRIPE_API_KEY"]}}}
---

# Stripe Operator

Operate a Stripe account safely: read-first, confirm before any mutating call.

## When to use

- The user asks about customers, payments, subscriptions, invoices, refunds, or products in Stripe.
- Debugging a failed payment or webhook.
- Building a revenue/billing report.

## Access

Use the REST API with the `STRIPE_API_KEY` environment variable (never print the key):

```bash
curl -s https://api.stripe.com/v1/customers?limit=10 -u "$STRIPE_API_KEY:" | jq '.data[] | {id, email, created}'
curl -s "https://api.stripe.com/v1/payment_intents?limit=20" -u "$STRIPE_API_KEY:" | jq '.data[] | {id, amount, currency, status}'
curl -s "https://api.stripe.com/v1/subscriptions?status=active&limit=20" -u "$STRIPE_API_KEY:" | jq '.data[] | {id, customer, status, current_period_end}'
curl -s "https://api.stripe.com/v1/invoices?limit=10" -u "$STRIPE_API_KEY:" | jq '.data[] | {id, customer, amount_due, status}'
```

If the `stripe` CLI is installed, prefer it for logs and webhook testing: `stripe listen`, `stripe trigger payment_intent.succeeded`, `stripe logs tail`.

## Workflow

1. Clarify the question (which object, which period, live or test mode — test keys start with `sk_test_`).
2. Query read endpoints first; paginate with `starting_after` when needed.
3. Aggregate locally (jq/python) for reports: MRR, churn, failed payments by reason.
4. For mutations (refunds, cancellations, coupon creation), show the exact call and ask for confirmation before executing.
5. Summarize amounts with currency and mode (test/live) clearly labeled.

## Guardrails

- Never create charges or refunds without explicit user confirmation.
- Never log or echo the API key.
- Flag when operating on live mode data.
