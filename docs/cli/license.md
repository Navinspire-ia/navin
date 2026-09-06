# License, account, and navin.live

The CLI does not bill. Stripe and the dashboard live on **navin.live**. The engine on this machine holds an activation token after Connect or `navin license activate`.

## What syncs

Synced: plan, seat/org, device list, managed-model budget, catalog for subscription models.

Not synced: chats, files, memories, your BYOK keys (they stay in `~/.navin/config.json`).

## Connect from the workbench

1. Open Navin (`navin .`).
2. Account → Connect. Browser opens `https://navin.live/<locale>/connect?state=...`.
3. Sign in and click Connect. The gateway polls and calls the same activate API as the CLI.

## Connect from the CLI

License key: dashboard → license key (`NAVIN-…`).

```bash
navin license activate NAVIN-XXXX-XXXX-XXXX-XXXX --name "office-pc"
navin license status
navin license deactivate
```

`deactivate` forgets the token and removes the **managed** OpenRouter key. A BYOK key you pasted yourself is kept.

## Device fingerprint

Built from a local `~/.navin/machine-id`, OS, arch, and home path, then hashed. Raw MAC/hostname are not sent.

CLI and desktop on the **same OS and home** count as **one** device. Windows (`irm`) and WSL (`curl`) are two homes → two devices.

Plan caps (devices): Free/Flash 2, Plus 3, Pro 5, Ultra 10. Team scales with seats. `device_limit_reached` means revoke an old device on the dashboard or upgrade.

## After activate

- Paid plans: site provisions a managed OpenRouter key with a monthly cap. Usage is reported after model calls; the server-side cap is the hard stop.
- Free: no managed key. Use BYOK or local models.
- `navin gateway` / the workbench refresh limits on a timer (about 10 minutes) so Stripe renewals apply without a restart.
- `/status` and Account in the sidebar show the plan.

## Errors (human text)

| Code | Meaning |
|---|---|
| `invalid_license` | Key not recognized |
| `subscription_inactive` / expired | Pay or wait for renew |
| `device_limit_reached` | Too many machines |
| `unauthorized_device` | Not activated or revoked |
| `account_suspended` | Reactivate on navin.live |
| `service_unavailable` | Transient; paid plans are not clamped to Free for a 5xx |

## Server URL

Default `https://navin.live`. Override only for local site work: `NAVIN_LICENSE_SERVER_URL` or `license.serverUrl`. Non-HTTPS (except localhost) is ignored.
