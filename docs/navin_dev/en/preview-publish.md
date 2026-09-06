# Publish preview

In the **Dev** module, the **Preview** tab can share your local development server on the Internet with a temporary public link (`*.trycloudflare.com`). Useful for demos, checking a site on a phone, or showing work to someone else - not production hosting.

No Cloudflare account is required. Navin starts the tunnel helper for you from the Preview toolbar.

## Auto-open Preview

When the agent scaffolds or starts a web app, Preview often opens by itself once the local server is ready. For Android, the **Mobile** tab can open the same way after a device preview starts. **Publish** (the public link) always stays a manual click.

On a narrow screen, the Dev chat becomes a bottom sheet so Preview can use the full width; auto-open can also collapse the chat so the app stays in focus.

## Usage

1. Start your project’s local preview (ask the agent, or use a terminal in the workbench if you prefer).
2. Open **Preview** (it may already be open) and confirm the local address shown there (for example a `127.0.0.1` URL with a port).
3. Click **Publish** next to **Open**.
4. Wait for the public `https://….trycloudflare.com` link to appear: copy it, open it, or **Stop** when you are done.

Navin picks the port from the local Preview URL and keeps the tunnel pointed at that address on your machine.

Requirements: Internet access. Publishing works from the same computer that is running Navin. On first use, Navin may download the tunnel helper automatically.

## What it is not

| Expectation | Reality |
| --- | --- |
| Permanent production hosting | No - temporary tunnel to your machine |
| Custom domain `mysite.com` | No - random `*.trycloudflare.com` URL |
| Cloudflare account required | No |

## Do I need a Cloudflare account?

| Goal | Cloudflare account? |
| --- | --- |
| Quick share via **Publish** | No |
| Your own domain / subdomain | Yes - outside this simple Publish flow |

## Troubleshooting

| Message or symptom | Likely cause | Hint |
| --- | --- | --- |
| Host not allowed in the local app | Stale tunnel state | Stop, then Publish again |
| Could not download the tunnel helper | Network blocked | Check Internet, or install the helper so Navin can find it |
| Nothing listening on that port | Dev server not running | Start the local preview, then Publish again |
| Need a URL with a port | Local address incomplete | Use a full local URL with an explicit port in Preview |
| Publish only on this machine | Trying from another device’s Navin | Publish from the computer that runs the project |

## Related

- [Workbench](./workbench.md) - Preview / Browser panel
