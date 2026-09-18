# Security policy

## Reporting a vulnerability

Please report security issues privately to **security@navinspire.com**.

A second channel is the GitHub private advisory form:
https://github.com/Navinspire-ia/navin/security/advisories/new

Do **not** file a public GitHub issue for an unreleased vulnerability. Public
issues are reserved for already-disclosed findings and CVE coordination only.

## What to include in the email

- Impact: what an attacker could achieve, who is affected, and how it scales.
- Affected versions: the exact releases, commits, or build channels involved.
- A safe reproduction outline that stays inside your own environment.

Do not send exploit payloads, leaked secrets, or reproduction commands that
could be weaponized. We will work with you on a safe exchange during triage.

## Coordinated disclosure

We follow coordinated disclosure and aim to publish a patch or advisory
within 90 days of confirmation. We are happy to agree on a longer embargo
when a fix requires it. We will credit reporters in the advisory unless you
ask to remain anonymous.

## Encryption

We do not publish a PGP key at this time. If you require encrypted email
for your report, mention it in your initial message and we will arrange a
secure channel.

## No secrets in public issues

Never paste API keys, tokens, passwords, license files, or
`~/.navin/config.json` contents into a public issue, discussion, or pull
request. They will be treated as compromised the moment they are visible.